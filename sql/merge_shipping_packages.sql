create or replace function public.merge_shipping_packages(
  p_request uuid, p_expected jsonb, p_new_package_id text
) returns integer language plpgsql security invoker set search_path=public as $$
declare
  ids bigint[];
  source_packages text[];
  row_count integer;
  before_rows jsonb;
  after_rows jsonb;
  item jsonb;
  current_row public.orders%rowtype;
begin
  if exists (select 1 from public.dispatch_events where event_id=p_request) then
    select cardinality(order_ids) into row_count from public.dispatch_events where event_id=p_request and kind='merge_shipping_packages';
    if row_count is null then raise exception '操作编号已被其他操作使用'; end if;
    return row_count;
  end if;
  if jsonb_typeof(p_expected) is distinct from 'array' or jsonb_array_length(p_expected)<2 then
    raise exception '请至少选择两个包裹';
  end if;
  select array_agg(distinct (x->>'id')::bigint),
         array_agg(distinct x->>'package_id')
    into ids,source_packages
    from jsonb_array_elements(p_expected) x;
  if cardinality(ids)<>jsonb_array_length(p_expected) or cardinality(source_packages)<2
     or exists(select 1 from unnest(source_packages) p where nullif(trim(p),'') is null) then
    raise exception '请选择至少两个不同且完整的包裹';
  end if;
  if nullif(trim(p_new_package_id),'') is null or p_new_package_id=any(source_packages)
     or exists(select 1 from public.orders where package_id=p_new_package_id) then
    raise exception '合拼包裹编号无效或已存在';
  end if;
  perform id from public.orders where package_id=any(source_packages) order by id for update;
  select count(*) into row_count from public.orders where package_id=any(source_packages);
  if row_count<>cardinality(ids) then raise exception '包裹订单已变动，请刷新后重试'; end if;
  for item in select value from jsonb_array_elements(p_expected) loop
    select * into current_row from public.orders where id=(item->>'id')::bigint;
    if not found or not (to_jsonb(current_row) @> item)
       or current_row.status not in ('官方已发货','已合包','已到达我方仓库')
       or current_row.shipping_fee is not null
       or current_row.taken_at is not null then
      raise exception '包裹内订单已变动或已付运费，请刷新后重试';
    end if;
  end loop;
  select jsonb_agg(to_jsonb(o) order by id) into before_rows from public.orders o where id=any(ids);
  update public.orders set package_id=p_new_package_id where id=any(ids);
  select jsonb_agg(to_jsonb(o) order by id) into after_rows from public.orders o where id=any(ids);
  insert into public.dispatch_events(event_id,kind,order_ids,reason,before_data,after_data)
    values(p_request,'merge_shipping_packages',ids, array_to_string(source_packages,' + '),before_rows,after_rows);
  return row_count;
end; $$;

create or replace function public.pay_shipping_package(
  p_request uuid, p_expected jsonb, p_total numeric
) returns integer language plpgsql security invoker set search_path=public as $$
declare
  ids bigint[];
  package_code text;
  row_count integer;
  cents bigint;
  before_rows jsonb;
  after_rows jsonb;
  item jsonb;
  current_row public.orders%rowtype;
begin
  if exists (select 1 from public.dispatch_events where event_id=p_request) then
    select cardinality(order_ids) into row_count from public.dispatch_events where event_id=p_request and kind='pay_shipping_package';
    if row_count is null then raise exception '操作编号已被其他操作使用'; end if;
    return row_count;
  end if;
  if jsonb_typeof(p_expected) is distinct from 'array' or jsonb_array_length(p_expected)<1 then
    raise exception '包裹内没有订单';
  end if;
  if p_total is null or p_total<0 or p_total<>round(p_total,2) then
    raise exception '运费须为非负金额，最多两位小数';
  end if;
  cents:=(p_total*100)::bigint;
  select array_agg(distinct (x->>'id')::bigint), min(x->>'package_id')
    into ids,package_code from jsonb_array_elements(p_expected) x;
  if cardinality(ids)<>jsonb_array_length(p_expected)
     or nullif(trim(package_code),'') is null
     or exists(select 1 from jsonb_array_elements(p_expected) x where x->>'package_id' is distinct from package_code) then
    raise exception '须选择一个完整包裹';
  end if;
  perform id from public.orders where package_id=package_code order by id for update;
  select count(*) into row_count from public.orders where package_id=package_code;
  if row_count<>cardinality(ids) then raise exception '包裹订单已变动，请刷新后重试'; end if;
  for item in select value from jsonb_array_elements(p_expected) loop
    select * into current_row from public.orders where id=(item->>'id')::bigint;
    if not found or not (to_jsonb(current_row) @> item)
       or current_row.status not in ('官方已发货','已合包','已到达我方仓库')
       or current_row.shipping_fee is not null
       or current_row.taken_at is not null then
      raise exception '包裹内订单已变动或已付运费，请刷新后重试';
    end if;
  end loop;
  select jsonb_agg(to_jsonb(o) order by id) into before_rows from public.orders o where id=any(ids);
  with numbered as (
    select id, row_number() over(order by id) as rn
      from public.orders where id=any(ids)
  )
  update public.orders o set status='在途',
     shipping_fee=((cents / row_count) + case when numbered.rn <= cents % row_count then 1 else 0 end)::numeric / 100
    from numbered where o.id=numbered.id;
  select jsonb_agg(to_jsonb(o) order by id) into after_rows from public.orders o where id=any(ids);
  insert into public.dispatch_events(event_id,kind,order_ids,reason,before_data,after_data)
    values(p_request,'pay_shipping_package',ids,'海外运费 HK$ '||p_total::text,before_rows,after_rows);
  return row_count;
end; $$;

revoke all on function public.merge_shipping_packages(uuid,jsonb,text) from public,anon,authenticated;
revoke all on function public.pay_shipping_package(uuid,jsonb,numeric) from public,anon,authenticated;
grant execute on function public.merge_shipping_packages(uuid,jsonb,text) to service_role;
grant execute on function public.pay_shipping_package(uuid,jsonb,numeric) to service_role;
notify pgrst, 'reload schema';
