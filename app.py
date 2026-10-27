import streamlit as st    
import pandas as pd
import datetime
import uuid
import time
import io
import hashlib
import csv
import re
import json
from decimal import Decimal, ROUND_HALF_UP
from supabase import create_client, Client

# 初始化 Supabase 连接
# Streamlit 会频繁 rerun；缓存 client 可以避免每次输入都重新建立连接。
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = get_supabase()


SHOPS = ["大号", "小号"]
PURCHASE_VENDORS = ["博客來", "金石堂", "官網", "bluelady", "城品"]
ORDER_FEATURE_SQL = """alter table public.orders
  add column if not exists shipping_notified_at timestamptz,
  add column if not exists purchase_vendor text;
notify pgrst, 'reload schema';"""

STATUSES = ["买家已下单", "我方已下单", "已合包", "在途", "已到货", "官方已发货", "卖家已发货", "已完结"]

# 分類和等待時間只使用現有 stock_type / order_time / deadline 欄位；不改 Supabase 表結構。
TYPE_NAMES = {"现货": "現貨", "预售": "預售", "混合": "現貨＋預售", "未标注": "未標註"}
FINISHED_STATUSES = {"卖家已发货", "已完结"}


def normalize_order_type(value):
    """舊資料或空值不猜測是現貨還是預售。"""
    if pd.isna(value):
        return "未标注"
    value = str(value).strip()
    if value in {"现货", "現貨"}:
        return "现货"
    if value in {"预售", "預售"}:
        return "预售"
    return "未标注"


def summarize_order_types(orders):
    """按實際商品類型分類買家；任何未標註記錄都不擅自判定。"""
    kinds = {normalize_order_type(x) for x in orders["stock_type"].tolist()}
    if not kinds or "未标注" in kinds:
        return "未标注"
    if len(kinds) > 1:
        return "混合"
    return next(iter(kinds))


def order_waiting_description(orders, today):
    """現貨仍按既有 15 天 deadline 顯示；預售只顯示實際已等天數。"""
    live = orders[~orders["status"].isin(FINISHED_STATUSES)]
    if live.empty:
        return "沒有待處理訂單"
    kind = live["stock_type"].map(normalize_order_type)
    pieces = []
    spot = live[kind.eq("现货")]
    if not spot.empty:
        deadlines = pd.to_datetime(spot["deadline"], errors="coerce").dropna()
        if deadlines.empty:
            pieces.append("現貨：未設定截止日期")
        else:
            remain = (deadlines.min().date() - today).days
            if remain < 0:
                pieces.append(f"現貨 🔴 超期 {-remain} 天")
            elif remain == 0:
                pieces.append("現貨 ⚠️ 今天截止")
            elif remain <= 5:
                pieces.append(f"現貨 🔥 剩 {remain} 天")
            else:
                pieces.append(f"現貨剩 {remain} 天")
    presale = live[kind.eq("预售")]
    if not presale.empty:
        dates = pd.to_datetime(presale["order_time"], errors="coerce").dropna()
        if dates.empty:
            pieces.append("預售：未記錄下單時間")
        else:
            waited = max(0, (today - dates.min().date()).days)
            pieces.append(f"預售已等待 {waited} 天")
    if kind.eq("未标注").any():
        pieces.append("有未標註類型的訂單")
    return " · ".join(pieces) if pieces else "未記錄期限"

st.set_page_config(page_title="图书销售云后台", layout="wide")

# 🎨 注入现代化 UI 样式：卡片化圆角、精美阴影、优化选项卡与表格质感
st.markdown("""
<style>
    /* 全局背景微调 */
    .stApp {
        background-color: #f8fafc;
    }
    
    /* 表单区域容器化：白色圆角卡片 + 细边框 + 微阴影 */
    div[data-testid="stForm"] {
        background-color: #ffffff;
        padding: 24px;
        border-radius: 16px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
    }
    
    /* 顶部指标卡片美化 */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        padding: 16px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.02);
    }
    
    /* 优化选项卡 Tab 样式：更具现代感 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 6px;
        background-color: #f1f5f9;
        padding: 6px;
        border-radius: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 40px;
        border-radius: 8px;
        font-weight: 500;
        color: #475569;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ffffff !important;
        color: #0f172a !important;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    
    /* 手機：主導覽可自然換行，按鈕有較大的點擊面積。 */
    div[data-testid="stRadio"] div[role="radiogroup"] {
        flex-wrap: wrap;
        gap: 0.35rem 0.75rem;
    }
    div[data-testid="stRadio"] label {
        padding-top: 0.25rem;
        padding-bottom: 0.25rem;
    }
    @media (max-width: 640px) {
        .block-container { padding-top: 1rem; }
        div[data-testid="stMetric"] { padding: 10px; }
    }
    /* 调整分割线更柔和 */
    hr {
        margin: 1.5rem 0;
        border-color: #e2e8f0;
    }
</style>
""", unsafe_allow_html=True)

st.title("📚 圖書訂單管理")

# ==========================================
# 🚀 数据加载优化
# ==========================================
# 只加载订单业务字段，不读取图片字段，减少数据库返回量和网络流量。
ORDER_COLUMNS = (
    "id,buyer_name,book_name,shop_name,status,"
    "price_sell,price_buy,shipping_fee,package_id,order_time,"
    "stock_type,deadline"
)

@st.cache_resource
def buyer_name_converter():
    try:
        from opencc import OpenCC
        return OpenCC("t2s.json")
    except ImportError:
        return None


@st.cache_resource
def buyer_alias_client():
    key = st.secrets.get("BUYER_ALIAS_SERVICE_KEY", "")
    return create_client(st.secrets["SUPABASE_URL"], key) if key else supabase


@st.cache_data(ttl=120, show_spinner=False)
def load_buyer_aliases():
    try:
        rows = []
        for offset in range(0, 1000000, 1000):
            page = buyer_alias_client().table("buyer_aliases").select("alias,canonical").order("alias").range(offset, offset + 999).execute().data or []
            rows.extend(page)
            if len(page) < 1000:
                return {r["alias"]: r["canonical"] for r in rows}, True
        raise ValueError("Too many aliases")
    except Exception:
        return {}, False


def canonical_buyer(name):
    name = str(name or "").strip()
    return BUYER_ALIASES.get(name, name)


def insert_buyer_orders(rows):
    normalized = [dict(row, buyer_name=canonical_buyer(row.get("buyer_name"))) for row in rows]
    return supabase.table("orders").insert(normalized).execute()


def buyer_name_candidates(names, converter):
    groups = {}
    for name in sorted(set(str(n) for n in names if pd.notna(n))):
        if not name.strip() or name.strip() in {"暂无", "暫無"}:
            continue
        key = converter.convert(name.strip())
        groups.setdefault(key, []).append(name)
    return [group for group in groups.values() if len(group) > 1]


def buyer_alias_payload(aliases, selected_names, canonical):
    selected = set(selected_names)
    if canonical not in selected or len(selected) < 2:
        raise ValueError("請選擇至少兩個名字，並從中選擇統一名稱。")
    # Keep every mapping flat, including previously confirmed aliases of these buyers.
    affected = {a for a, target in aliases.items() if target in selected or a in selected} | selected
    return [{"alias": a, "canonical": canonical} for a in sorted(affected)]


def render_buyer_alias_manager(orders):
    with st.expander("👥 買家簡繁識別與合併", expanded=False):
        st.caption("自動配對只提供建議。請核對是否同一人；確認後各面板會合併顯示，新訂單也會沿用已確認名稱。原有訂單不會刪除或改寫。")
        converter = buyer_name_converter()
        if converter is None:
            st.warning("尚未安裝 OpenCC：請在 requirements.txt 另加一行 opencc，重新啟動程式。本機可執行 python -m pip install opencc。")
        if not BUYER_ALIASES_AVAILABLE:
            st.info("別名記憶尚未啟用。先完成下方一次性設定，再確認合併。")
        with st.expander("首次設定：啟用永久別名記憶"):
            st.write("1. 在 Supabase SQL Editor 執行以下 SQL。")
            st.code("""create table if not exists public.buyer_aliases (
    alias text primary key,
    canonical text not null,
    check (length(trim(alias)) > 0),
    check (length(trim(canonical)) > 0)
);
alter table public.buyer_aliases enable row level security;
revoke all on public.buyer_aliases from anon, authenticated;
grant select, insert, update, delete on public.buyer_aliases to service_role;""", language="sql")
            st.write("2. 若目前 SUPABASE_KEY 不是 service_role 金鑰，請在 Streamlit Secrets 新增 BUYER_ALIAS_SERVICE_KEY，填入同一 Supabase 專案的 service_role 金鑰。只放在 Secrets，不要貼在程式碼或聊天中。")
            st.write("3. 按下方重新檢查；顯示已啟用後即可使用。")
            if st.button("重新檢查別名設定", key="alias_recheck"):
                load_buyer_aliases.clear()
                buyer_alias_client.clear()
                st.rerun()
        if BUYER_ALIASES_AVAILABLE:
            st.success("永久別名記憶已啟用")
        if orders.empty:
            return
        names = sorted(orders["buyer_name"].dropna().astype(str).unique().tolist())
        candidates = buyer_name_candidates(names, converter) if converter else []
        mode = st.radio("核對方式", ["自動找簡繁重名", "手動選擇名字"], horizontal=True, key="alias_mode")
        chosen_names = []
        if mode == "自動找簡繁重名":
            if candidates:
                index = st.selectbox("疑似同一買家", list(range(len(candidates))), format_func=lambda i: " ／ ".join(candidates[i]), key="alias_candidate")
                chosen_names = st.multiselect("本次要歸為同一人的名字", candidates[index], default=candidates[index], key=f"alias_names_{hashlib.sha256('|'.join(candidates[index]).encode()).hexdigest()[:12]}")
            elif converter:
                st.info("目前沒有找到簡繁轉換後相同的不同名字。也可切換手動選擇。")
        else:
            chosen_names = st.multiselect("本次要歸為同一人的名字", names, key="alias_manual_names")
        if len(chosen_names) >= 2:
            subset = orders[orders["buyer_name"].isin(chosen_names)]
            st.dataframe(subset[[c for c in ["id", "buyer_name_original", "buyer_name", "book_name", "shop_name", "order_time", "status", "price_sell"] if c in subset.columns]].rename(columns={"buyer_name_original":"原始名字", "buyer_name":"目前買家名稱"}), hide_index=True, use_container_width=True)
            canonical = st.selectbox("統一顯示名稱", chosen_names, key="alias_canonical")
            token = hashlib.sha256(("|".join(sorted(chosen_names)) + "|" + canonical).encode()).hexdigest()[:16]
            confirmed = st.checkbox("已核對以上訂單，確認所選名字是同一買家", key=f"alias_confirm_{token}")
            if st.button("確認合併並記住別名", disabled=not (confirmed and BUYER_ALIASES_AVAILABLE), key=f"alias_save_{token}"):
                try:
                    payload = buyer_alias_payload(BUYER_ALIASES, chosen_names, canonical)
                    buyer_alias_client().table("buyer_aliases").upsert(payload, on_conflict="alias").execute()
                    load_buyer_aliases.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"別名保存失敗，請重新檢查設定：{exc}")
        if BUYER_ALIASES:
            st.markdown("**已確認的別名**")
            st.dataframe(pd.DataFrame([{"輸入名字":a, "統一名稱":c} for a,c in sorted(BUYER_ALIASES.items())]), hide_index=True, use_container_width=True)
            st.caption("若合錯，可撤回整組別名。原始訂單會恢復各自名稱；合併後新增的訂單已用統一名稱保存，須在訂單編輯中另行更正。")
            target = st.selectbox("要撤回的統一名稱", sorted(set(BUYER_ALIASES.values())), key="alias_undo_target")
            undo_ok = st.checkbox("確認撤回這組別名", key=f"alias_undo_{target}")
            if st.button("撤回別名合併", disabled=not undo_ok, key="alias_undo_button"):
                try:
                    buyer_alias_client().table("buyer_aliases").delete().eq("canonical", target).execute()
                    load_buyer_aliases.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"撤回失敗：{exc}")


DISPATCH_SQL = "alter table public.orders\n add column if not exists recipient_name text,\n add column if not exists postage_paid_at timestamptz,\n add column if not exists taken_at timestamptz,\n add column if not exists packed_at timestamptz,\n add column if not exists packing_ref text,\n add column if not exists allocation_note text,\n add column if not exists shipping_notified_at timestamptz,\n add column if not exists purchase_vendor text;\n\ncreate table if not exists public.dispatch_events (\n event_id uuid primary key,\n created_at timestamptz not null default now(),\n kind text not null,\n order_ids bigint[] not null,\n reason text not null default '',\n before_data jsonb not null,\n after_data jsonb not null\n);\nalter table public.dispatch_events enable row level security;\nrevoke all on public.dispatch_events from anon, authenticated;\ngrant select, insert on public.dispatch_events to service_role;\n\ncreate or replace function public.dispatch_update(\n p_request uuid, p_expected jsonb, p_action text,\n p_value text default '', p_reason text default ''\n) returns uuid language plpgsql security invoker set search_path=public as $$\ndeclare\n ids bigint[]; n integer; r public.orders%rowtype; expected jsonb;\n before_rows jsonb; after_rows jsonb; stamp timestamptz:=now(); ref text;\nbegin\n perform pg_advisory_xact_lock(hashtextextended(p_request::text,0));\n if exists(select 1 from public.dispatch_events where event_id=p_request) then return p_request; end if;\n if jsonb_typeof(p_expected) is distinct from 'array' or jsonb_array_length(p_expected)=0 then raise exception '请选择订单'; end if;\n select array_agg(distinct (x->>'id')::bigint) into ids from jsonb_array_elements(p_expected) x;\n if cardinality(ids)<>jsonb_array_length(p_expected) then raise exception '订单编号重复'; end if;\n perform id from public.orders where id=any(ids) order by id for update;\n select count(*) into n from public.orders where id=any(ids);\n if n<>cardinality(ids) then raise exception '部分订单已不存在'; end if;\n for expected in select value from jsonb_array_elements(p_expected) loop\n  select * into r from public.orders where id=(expected->>'id')::bigint;\n  if not (to_jsonb(r) @> expected) then raise exception '订单 #% 已变动，请刷新后重试',r.id; end if;\n  if r.taken_at is not null or r.status in ('卖家已发货','已完结') then raise exception '订单 #% 已带走或已发货，不可修改分配',r.id; end if;\n  if p_action in ('notify','unnotify','pack','unpack','take') and r.status is distinct from '已到货' then raise exception '订单 #% 尚未签收',r.id; end if;\n  if p_action='take' and (r.postage_paid_at is null or nullif(trim(r.recipient_name),'') is null or r.packed_at is null) then raise exception '订单 #% 带走前须已补邮、已填写收件人并确认打包',r.id; end if;\n end loop;\n select jsonb_agg(to_jsonb(o) order by id) into before_rows from public.orders o where id=any(ids);\n case p_action\n when 'recipient' then\n  if nullif(trim(p_value),'') is null then raise exception '请填写收件人姓名'; end if;\n  update public.orders set\n   packed_at=case when recipient_name is distinct from trim(p_value) then null else packed_at end,\n   packing_ref=case when recipient_name is distinct from trim(p_value) then null else packing_ref end,\n   recipient_name=trim(p_value) where id=any(ids);\n when 'notify' then update public.orders set shipping_notified_at=coalesce(shipping_notified_at,stamp) where id=any(ids);\n when 'unnotify' then update public.orders set shipping_notified_at=null where id=any(ids);\n when 'pay' then update public.orders set postage_paid_at=coalesce(postage_paid_at,stamp) where id=any(ids);\n when 'unpay' then update public.orders set postage_paid_at=null where id=any(ids);\n when 'pack' then\n  ref:=coalesce(nullif(trim(p_value),''),'打包-'||substr(p_request::text,1,8));\n  update public.orders set packed_at=stamp,packing_ref=ref where id=any(ids);\n when 'unpack' then update public.orders set packed_at=null,packing_ref=null where id=any(ids);\n when 'take' then update public.orders set taken_at=stamp,status='卖家已发货' where id=any(ids);\n else raise exception '不支持的操作';\n end case;\n select jsonb_agg(to_jsonb(o) order by id) into after_rows from public.orders o where id=any(ids);\n insert into public.dispatch_events(event_id,kind,order_ids,reason,before_data,after_data)\n values(p_request,p_action,ids,coalesce(p_reason,''),before_rows,after_rows);\n return p_request;\nend; $$;\nrevoke all on function public.dispatch_update(uuid,jsonb,text,text,text) from public,anon,authenticated;\ngrant execute on function public.dispatch_update(uuid,jsonb,text,text,text) to service_role;\n\ncreate or replace function public.dispatch_swap(\n p_request uuid, p_a jsonb, p_b jsonb, p_mode text,\n p_reason text, p_same_item boolean, p_packed_ack boolean, p_notice_ack boolean\n) returns uuid language plpgsql security invoker set search_path=public as $$\ndeclare\n a public.orders%rowtype; b public.orders%rowtype;\n aid bigint:=(p_a->>'id')::bigint; bid bigint:=(p_b->>'id')::bigint;\n before_rows jsonb; after_rows jsonb; arrived_package text; was_packed boolean;\nbegin\n perform pg_advisory_xact_lock(hashtextextended(p_request::text,0));\n if exists(select 1 from public.dispatch_events where event_id=p_request) then return p_request; end if;\n if aid is null or bid is null or aid=bid then raise exception '请选择两笔不同订单'; end if;\n if not coalesce(p_same_item,false) or nullif(trim(p_reason),'') is null then raise exception '请核对同书同版本并填写原因'; end if;\n perform id from public.orders where id in (aid,bid) order by id for update;\n select * into a from public.orders where id=aid;\n if not found then raise exception '接货订单不存在'; end if;\n select * into b from public.orders where id=bid;\n if not found then raise exception '让货订单不存在'; end if;\n if not (to_jsonb(a) @> p_a) or not (to_jsonb(b) @> p_b) then raise exception '订单已变动，请刷新后重新核对'; end if;\n if a.taken_at is not null or b.taken_at is not null or a.status in ('卖家已发货','已完结') or b.status is distinct from '已到货' then raise exception '已带走或未签收的实物不能让货'; end if;\n if a.status is null or a.status not in ('我方已下单','官方已发货','已合包','在途','已到达我方仓库','已到货') then raise exception '接货订单须已采购'; end if;\n if coalesce(trim(a.buyer_name),'') in ('','暂无','暫無') or coalesce(trim(b.buyer_name),'') in ('','暂无','暫無') or a.buyer_name=b.buyer_name then raise exception '请选择不同买家'; end if;\n if b.postage_paid_at is not null then raise exception '让货订单已补邮，不可让给其他人'; end if;\n if p_mode='paid' then\n  if a.postage_paid_at is null then raise exception '接货买家尚未补邮'; end if;\n elsif p_mode='fifo' then\n  if a.status='已到货' or a.order_time is null or b.order_time is null or (a.order_time,a.id)>=(b.order_time,b.id) then raise exception '先下单优先仅适用于更早下单且未到货的订单'; end if;\n elsif p_mode<>'manual' or p_mode is null then raise exception '未知调货方式'; end if;\n was_packed:=a.packed_at is not null or b.packed_at is not null;\n if was_packed and not coalesce(p_packed_ack,false) then raise exception '已打包：请确认拆包、重新核对收件人及更换面单'; end if;\n if b.shipping_notified_at is not null and not coalesce(p_notice_ack,false) then raise exception '让货买家已收到通知，请确认延期安排'; end if;\n if a.status<>'已到货' and coalesce(trim(a.package_id),'')='' then raise exception '请先补齐接货订单的待到货包裹编号'; end if;\n arrived_package:=b.package_id;\n if coalesce(trim(arrived_package),'')='' or (arrived_package=a.package_id and a.status<>'已到货') then arrived_package:='调货已到-'||p_request::text; end if;\n before_rows:=jsonb_build_array(to_jsonb(a),to_jsonb(b));\n update public.orders set status=b.status,package_id=arrived_package,price_buy=b.price_buy,\n  shipping_fee=b.shipping_fee,purchase_vendor=b.purchase_vendor,\n  shipping_notified_at=null,packed_at=null,packing_ref=null,\n  allocation_note='已调入订单 #'||b.id||' 的实物；'||p_reason where id=a.id;\n update public.orders set status=a.status,package_id=a.package_id,price_buy=a.price_buy,\n  shipping_fee=a.shipping_fee,purchase_vendor=a.purchase_vendor,\n  shipping_notified_at=null,packed_at=null,packing_ref=null,\n  allocation_note=case when a.status='已到货' then '已让出原打包实物，接手另一已到货件；' else '调货后待到货，接手包裹 '||a.package_id||'；' end||p_reason where id=b.id;\n -- Buyer identity, recipient, sale price, order time and postage payment stay with each order.\n select jsonb_agg(to_jsonb(o) order by id) into after_rows from public.orders o where id in (aid,bid);\n insert into public.dispatch_events(event_id,kind,order_ids,reason,before_data,after_data)\n values(p_request,'swap_'||p_mode,array[aid,bid],p_reason,before_rows,after_rows);\n return p_request;\nend; $$;\nrevoke all on function public.dispatch_swap(uuid,jsonb,jsonb,text,text,boolean,boolean,boolean) from public,anon,authenticated;\ngrant execute on function public.dispatch_swap(uuid,jsonb,jsonb,text,text,boolean,boolean,boolean) to service_role;\n-- Retire the old swap entry point: it did not guard postage payment or taken items.\ndrop function if exists public.swap_order_allocation(uuid,bigint,bigint,jsonb,jsonb,timestamptz,timestamptz,text,boolean);\nnotify pgrst,'reload schema';\n"
DISPATCH_FIELDS = ['recipient_name', 'postage_paid_at', 'taken_at', 'packed_at', 'packing_ref', 'allocation_note']

def notification_summary(orders):
    count = int(orders["shipping_notified_at"].notna().sum())
    state = "未通知" if count == 0 else ("已通知發貨" if count == len(orders) else "部分已通知")
    return state, count


@st.cache_data(ttl=120, show_spinner=False)
def load_data():
    try:
        feature_ready = True
        try:
            supabase.table("orders").select("shipping_notified_at,purchase_vendor").limit(1).execute()
        except Exception as exc:
            message = str(exc)
            if ("42703" in message or "PGRST204" in message) and any(field in message for field in ("shipping_notified_at", "purchase_vendor")):
                feature_ready = False
            else:
                raise
        columns = ORDER_COLUMNS + ",shipping_notified_at,purchase_vendor" if feature_ready else ORDER_COLUMNS
        dispatch_ready = True
        try:
            supabase.table("orders").select(",".join(DISPATCH_FIELDS)).limit(1).execute()
        except Exception as exc:
            if "42703" in str(exc) or "PGRST204" in str(exc):
                dispatch_ready = False
            else:
                raise
        if dispatch_ready:
            columns += "," + ",".join(DISPATCH_FIELDS)
        rows = []
        page_size = 1000
        offset = 0

        while True:
            response = (
                supabase.table("orders")
                .select(columns)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        result = pd.DataFrame(rows)
        result.attrs["order_features_ready"] = feature_ready
        result.attrs["dispatch_ready"] = dispatch_ready and feature_ready
        result['_dispatch_snapshot'] = pd.Series([dict(row) for row in rows], index=result.index, dtype=object)
        for field in DISPATCH_FIELDS:
            if field not in result:
                result[field] = pd.Series(index=result.index, dtype=object)
            if field.endswith('_at'):
                result[field] = pd.to_datetime(result[field], errors='coerce', utc=True)
            else:
                result[field] = result[field].fillna('')
        if "shipping_notified_at" not in result:
            result["shipping_notified_at"] = pd.Series(index=result.index, dtype="object")
        result["shipping_notified_at"] = pd.to_datetime(result["shipping_notified_at"], errors="coerce", utc=True)
        if "purchase_vendor" not in result:
            result["purchase_vendor"] = ""
        result["purchase_vendor"] = result["purchase_vendor"].fillna("")

        if not result.empty and "order_time" in result.columns:
            result["order_time"] = pd.to_datetime(result["order_time"], errors="coerce")
            result = result.sort_values("order_time", ascending=True)

        for col in ["price_sell", "price_buy", "shipping_fee"]:
            if col in result.columns:
                result[col] = pd.to_numeric(result[col], errors="coerce").fillna(0.0)

        return result

    except Exception as e:
        st.error(f"⚠️ 数据加载失败，请检查网络或数据库状态：{e}")
        return pd.DataFrame()




def clear_cached_data():
    load_data.clear()


def batch_update_orders(order_ids, values):
    """把多次逐笔 update 合并成一次请求。"""
    ids = [int(x) for x in order_ids if str(x).strip()]
    if not ids:
        return None
    query = supabase.table("orders").update(values).in_("id", ids)
    if globals().get("DISPATCH_READY", False):
        query = query.is_("taken_at", "null").not_.in_("status", list(FINISHED_STATUSES))
    return query.execute()


SWAP_WAITING_STATUSES = {"我方已下单", "官方已发货", "已合包", "在途", "已到达我方仓库"}




def swap_book_key(name):
    # Whitespace/parentheses and traditional/simplified spelling are presentation
    # differences. Edition remains part of the key; special and standard never mix.
    text = str(name or "").replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s\u200b\ufeff]+", "", text)
    converter = buyer_name_converter()
    return converter.convert(text) if converter else text






def filter_swap_order_search(orders, query):
    query = str(query or "").strip()
    if not query:
        return orders
    number = query.lstrip("#＃").strip()
    if number.isdecimal():
        return orders[pd.to_numeric(orders["id"], errors="coerce").eq(int(number))]
    columns = [c for c in ["buyer_name", "buyer_name_original", "book_name"] if c in orders.columns]
    matched = orders[columns].fillna("").astype(str).apply(
        lambda col: col.str.contains(query, case=False, regex=False)
    ).any(axis=1)
    return orders[matched]




def dispatch_label(row):
    return f"#{int(row['id'])} · {row['buyer_name']} · {row['book_name']} · {row['status']} · 收件人：{row.get('recipient_name') or '未填'}"


def dispatch_call(name, params):
    # A retry of the same reviewed operation must not execute the swap twice.
    token = hashlib.sha256(json.dumps([name, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = 'dispatch_request_' + token
    if key not in st.session_state:
        st.session_state[key] = str(uuid.uuid4())
    try:
        buyer_alias_client().rpc(name, {'p_request': st.session_state[key], **params}).execute()
        del st.session_state[key]
        clear_cached_data()
        st.session_state['dispatch_success'] = ('已保存。调货后请重新核对打包、收件人及面单。' if name == 'dispatch_swap' else ('已登记发货，已发订单已从名字板移除。' if params.get('p_action') == 'take' else '已保存所选订单的记录。'))
        st.rerun()
    except Exception as exc:
        clear_cached_data()
        st.error(f'保存失败：{exc}。请刷新后重新核对；若提示函数不存在，请执行下方升级 SQL。')


def dispatch_donors(orders, target, mode):
    candidates = orders[
        orders['book_name'].map(swap_book_key).eq(swap_book_key(target['book_name']))
        & orders['buyer_name'].ne(target['buyer_name'])
        & ~orders['buyer_name'].isin(['', '暂无', '暫無'])
        & orders['status'].eq('已到货') & orders['taken_at'].isna()
        & orders['postage_paid_at'].isna()
    ].copy()
    if mode == 'fifo':
        if pd.isna(target['order_time']) or target['status'] == '已到货':
            return candidates.iloc[0:0]
        candidates = candidates[(candidates['order_time'] > target['order_time']) | ((candidates['order_time'] == target['order_time']) & (candidates['id'] > target['id']))]
    if mode == 'paid' and pd.isna(target['postage_paid_at']):
        return candidates.iloc[0:0]
    if target['status'] == '已到货':
        # Already-arrived targets only need to exchange a loose copy for a packed one.
        candidates = candidates[candidates['packed_at'].notna()]
        if pd.notna(target['packed_at']):
            return candidates.iloc[0:0]
    return candidates.sort_values(['order_time', 'id'], ascending=False, kind='stable')


def render_allocation_swap(orders):
    with st.expander('🔄 同款调货：先下单／已补邮优先', expanded=False):
        st.caption('同书同版本可跨预售和现货。每次核对后调一本，付款和收件人留在各自订单；未补邮买家的待发记录保留。已补邮、已带走的订单不会让货。')
        mode_label = st.radio('调货方式', ['先下单优先', '已补邮优先', '手动加急'], horizontal=True)
        mode = {'先下单优先':'fifo', '已补邮优先':'paid', '手动加急':'manual'}[mode_label]
        active = orders[orders['status'].isin(['我方已下单','官方已发货','已合包','在途','已到达我方仓库','已到货']) & orders['taken_at'].isna() & ~orders['buyer_name'].isin(['','暂无','暫無'])]
        search = st.text_input('查找接货订单：输入编号（如 272）、买家或书名', key='dispatch_swap_search')
        targets = filter_swap_order_search(active, search).sort_values(['order_time', 'id'], kind='stable')
        targets = targets[targets.apply(lambda r: not dispatch_donors(orders, r, mode).empty, axis=1)] if not targets.empty else targets
        if targets.empty:
            st.info('没有符合条件的调货：请核对同书同版本、已签收库存及补邮状态。先下单优先仅显示有较晚买家可让货的订单；已到货买家可接手未补邮买家的已打包件。')
        else:
            target_id = st.selectbox('接货订单（按下单先后排列）', targets['id'].tolist(), format_func=lambda i: dispatch_label(targets.loc[targets['id'].eq(i)].iloc[0]))
            a = targets.loc[targets['id'].eq(target_id)].iloc[0]
            donors = dispatch_donors(orders, a, mode)
            donor_id = st.selectbox('让货订单（已签收且未补邮）', donors['id'].tolist(), format_func=lambda i: dispatch_label(donors.loc[donors['id'].eq(i)].iloc[0]))
            b = donors.loc[donors['id'].eq(donor_id)].iloc[0]
            st.write(f"接货：#{int(a['id'])} {a['buyer_name']} ← #{int(b['id'])} {b['buyer_name']} 的实物")
            st.write(f"让货后：{b['buyer_name']} 接手包裹 {a.get('package_id') or '未填写'}，状态为 {a['status']}。")
            st.caption(f"原打包标记：{b.get('packing_ref') or '未打包'}。调货会清除双方打包及通知标记，补邮记录不会交换。")
            pair_key = f"{int(a['id'])}_{int(b['id'])}_{mode}"
            same = st.checkbox('已核对同书、同版本及附带赠品一致', key='same_'+pair_key)
            packed = pd.notna(a['packed_at']) or pd.notna(b['packed_at'])
            packed_ack = st.checkbox('已确认拆包、重新核对收件人和更换面单', key='packack_'+pair_key) if packed else True
            notice_ack = st.checkbox('让货买家已通知过，我已处理延期安排', key='noticeack_'+pair_key) if pd.notna(b['shipping_notified_at']) else True
            reason = st.text_input('调货原因', value=mode_label, key='reason_'+pair_key)
            if st.button('确认调货一本', disabled=not (same and packed_ack and notice_ack and reason.strip()), key='swap_'+pair_key):
                dispatch_call('dispatch_swap', {'p_a':a['_dispatch_snapshot'], 'p_b':b['_dispatch_snapshot'], 'p_mode':mode, 'p_reason':reason.strip(), 'p_same_item':same, 'p_packed_ack':packed_ack, 'p_notice_ack':notice_ack})
        with st.expander('调货及发货操作记录（最近 100 次）'):
            if not st.checkbox('加载操作记录', key='dispatch_load_history'):
                return
            try:
                events = buyer_alias_client().table('dispatch_events').select('created_at,kind,order_ids,reason').order('created_at', desc=True).limit(100).execute().data
                st.dataframe(pd.DataFrame(events), hide_index=True, use_container_width=True)
            except Exception as exc:
                st.info(f'请完成升级 SQL 及服务端密钥设置后查看记录：{exc}')


def render_dispatch_panel(orders):
    import html
    st.markdown('''<style>
    .clip-heading{font-family:serif;color:#193b50;font-size:2.5rem;font-weight:800;margin:0}
    .clip-subtitle{color:#867b6c;margin-bottom:1rem}
    div[data-testid="stVerticalBlockBorderWrapper"]:has(.clip-name),
    div[data-testid="stVerticalBlock"]:has(> div > div > div > .clip-name){border-radius:12px}
    .clip-name{position:relative;background:linear-gradient(135deg,#fffdf7,#f3ebdc);border:1px solid #e3d7c4;border-radius:9px;padding:30px 10px 16px;margin-top:18px;text-align:center;color:#193b50;box-shadow:0 6px 15px #213c4c12}
    .clip-name:before{content:"";position:absolute;top:-15px;left:calc(50% - 12px);width:24px;height:26px;border:2px solid #867354;border-radius:4px;background:linear-gradient(90deg,#b3a384,#eee3c8,#ab9978);box-shadow:0 2px 2px #0002}
    .clip-name:after{content:"";position:absolute;top:-27px;left:calc(50% - 7px);width:14px;height:18px;border:2px solid #867354;border-radius:8px;background:transparent}
    .clip-buyer{font-size:1.65rem;font-weight:750;line-height:1.4;overflow-wrap:anywhere;min-height:2.4rem}
    .clip-manifest{margin-top:14px;text-align:left;font-size:.88rem;border-top:1px solid #dfd3bd;padding-top:10px;overflow-wrap:anywhere}
    .clip-recipient{font-weight:700;margin:8px 0 4px}.clip-book{margin:4px 0;line-height:1.5}.clip-name:before,.clip-name:after{pointer-events:none}
    .clip-state{font-size:.85rem;color:#46756e;margin-top:10px}.clip-state.wait{color:#ad792b}
    .clip-rail{height:3px;background:linear-gradient(90deg,#d8c8a7,#a78e68,#d8c8a7);margin:26px 0 0;border-radius:3px}
    </style>''', unsafe_allow_html=True)
    st.markdown('<div class="clip-heading">发货夹子板</div><div class="clip-subtitle">名字挂在这里，发完就收走。</div>', unsafe_allow_html=True)
    if st.button('刷新', key='refresh_shipping'):
        clear_cached_data()
        st.rerun()
    if st.session_state.get('dispatch_success'):
        st.success(st.session_state.pop('dispatch_success'))
    if not DISPATCH_READY or st.checkbox('显示数据库升级说明', key='clip_setup'):
        with st.expander('首次升级：复制 SQL 到 Supabase 执行', expanded=not DISPATCH_READY):
            st.write('在订单程序对应的 Supabase 项目 → SQL Editor 执行下面整段 SQL，再刷新。')
            st.code(DISPATCH_SQL, language='sql')
            st.caption('如需服务端密钥，在 Streamlit Secrets 设置同项目 BUYER_ALIAS_SERVICE_KEY。')
    if not DISPATCH_READY:
        st.info('请先完成数据库升级。')
        return
    if orders.empty:
        st.info('暂无订单。')
        return
    view = st.radio('页面', ['名字发货板', '收件人／补邮登记'], horizontal=True, key='clip_view')
    if st.checkbox('需要调货时打开', key='dispatch_enable_swap'):
        render_allocation_swap(orders)
    search = st.text_input('搜索名字、收件人或订单号', placeholder='例如：272', key='dispatch_search')
    state = st.radio('补邮状态', ['全部', '已补邮', '待补邮'], horizontal=True, key='clip_state')
    edition = st.radio('预售版本', ['全部', '特装', '普装', '官网特'], horizontal=True, key='clip_edition')
    live = orders[orders['taken_at'].isna() & ~orders['status'].isin(FINISHED_STATUSES) & ~orders['buyer_name'].isin(['', '暂无', '暫無'])]
    rows = live[live['status'].eq('已到货')] if view == '名字发货板' else live
    if search.strip():
        found = filter_swap_order_search(rows, search)
        if not search.strip().lstrip('#＃').isdecimal():
            found = rows[rows.index.isin(found.index) | rows['recipient_name'].str.contains(search.strip(), regex=False, case=False, na=False)]
        rows = found
    if edition != '全部':
        rows = rows[rows['stock_type'].eq('预售') & rows['book_name'].map(lambda n: presale_name_and_edition(n)[1]).eq(edition)]
    # Status chips choose whole cards, so a partially paid buyer never looks fully paid.
    if state != '全部' and not rows.empty:
        paid_by_buyer = rows.groupby('buyer_name')['postage_paid_at'].apply(lambda c: c.notna().all())
        eligible = paid_by_buyer[paid_by_buyer if state == '已补邮' else ~paid_by_buyer].index
        rows = rows[rows['buyer_name'].isin(eligible)]
    if rows.empty:
        st.success('当前没有待处理的名字。')
        return
    rows = rows.sort_values(['order_time', 'id'], kind='stable')
    buyers = rows['buyer_name'].drop_duplicates().tolist()
    page_count = max(1, (len(buyers) + 19) // 20)
    filter_key = hashlib.sha256(json.dumps([view, search, edition, state], ensure_ascii=False).encode()).hexdigest()[:12]
    page_key = 'clip_page_' + filter_key
    if st.session_state.get(page_key, 1) > page_count:
        st.session_state[page_key] = page_count
    page = st.selectbox('每页 20 人', list(range(1, page_count + 1)), key=page_key)
    st.caption(f'待处理 {len(buyers)} 人 · 第 {page}/{page_count} 页。按钮仅处理当前筛选范围内的订单；发货后移出本页，记录保留在订单管理。')
    page_buyers = buyers[(page - 1) * 20:page * 20]
    groups = dict(tuple(rows[rows['buyer_name'].isin(page_buyers)].groupby('buyer_name', sort=False)))
    waiting_counts = live[~live['status'].eq('已到货')].groupby('buyer_name').size().to_dict()
    for offset in range(0, len(page_buyers), 4):
        st.markdown('<div class="clip-rail"></div>', unsafe_allow_html=True)
        columns = st.columns(4)
        for column, buyer in zip(columns, page_buyers[offset:offset+4]):
            group = groups[buyer]
            key = hashlib.sha256(str(buyer).encode()).hexdigest()[:16]
            unpaid = group[group['postage_paid_at'].isna()]
            arrived = bool(group['status'].eq('已到货').all())
            recipient_ready = bool(group['recipient_name'].fillna('').str.strip().ne('').all())
            packed = bool(group['packed_at'].notna().all())
            ready = arrived and unpaid.empty and recipient_ready and packed
            badge = '已补邮' if unpaid.empty else ('待补邮' if len(unpaid) == len(group) else '部分已补邮')
            with column:
                manifest = clip_manifest_html(group)
                notice_count = int(group['shipping_notified_at'].notna().sum())
                notice_label = '已通知' if notice_count == len(group) else ('未通知' if notice_count == 0 else '部分已通知')
                st.markdown(f'<div class="clip-name"><div class="clip-buyer">{html.escape(str(buyer))}</div><div class="clip-state {"wait" if not unpaid.empty else ""}">● {badge} · {notice_label} · {len(group)} 本</div><div class="clip-manifest">{manifest}</div></div>', unsafe_allow_html=True)
                if arrived and notice_count < len(group):
                    if st.button('标记已通知', key='quick_notice_'+key, use_container_width=True):
                        pending_notice = group[group['shipping_notified_at'].isna()]
                        dispatch_call('dispatch_update', {'p_expected':pending_notice['_dispatch_snapshot'].tolist(), 'p_action':'notify', 'p_value':'', 'p_reason':'名字板：标记已通知'})
                if waiting_counts.get(buyer) and view == '名字发货板':
                    st.caption(f'另有 {waiting_counts[buyer]} 本未到，本次仅处理已到货件')
                if not unpaid.empty:
                    if st.button('标记补邮', key='quick_pay_'+key, use_container_width=True):
                        dispatch_call('dispatch_update', {'p_expected':unpaid['_dispatch_snapshot'].tolist(), 'p_action':'pay', 'p_value':'', 'p_reason':'名字板：标记补邮'})
                if arrived:
                    if st.button('发货', key='quick_ship_'+key, type='primary', disabled=not ready, use_container_width=True):
                        dispatch_call('dispatch_update', {'p_expected':group['_dispatch_snapshot'].tolist(), 'p_action':'take', 'p_value':'', 'p_reason':'名字板：直接登记发货'})
                    if not recipient_ready:
                        st.caption('先在「查看订单」填写收件人')
                    if not packed:
                        if st.button('确认打包／面单', key='quick_pack_'+key, use_container_width=True):
                            dispatch_call('dispatch_update', {'p_expected':group['_dispatch_snapshot'].tolist(), 'p_action':'pack', 'p_value':'', 'p_reason':'名字板：已核对打包及面单'})
                if st.button('查看订单／修改', key='clip_details_'+key, use_container_width=True):
                    if st.session_state.get('clip_detail_buyer') == buyer:
                        st.session_state.pop('clip_detail_buyer', None)
                    else:
                        st.session_state['clip_detail_buyer'] = buyer
        # Render directly below the clicked row, rather than after all 20 cards.
        detail_buyer = st.session_state.get('clip_detail_buyer')
        if detail_buyer in page_buyers[offset:offset+4]:
            with st.container():
                st.info(f'正在查看：{detail_buyer}。可填写收件人、分单及撤回标记。')
                render_dispatch_details(groups[detail_buyer], detail_buyer)


def clip_manifest_html(group):
    """The visible manifest and action payload always share the same rows."""
    import html
    shown = group.copy()
    shown['_recipient'] = shown['recipient_name'].fillna('').astype(str).str.strip().replace('', '未填写收件人')
    blocks = []
    for recipient, recipient_orders in shown.groupby('_recipient', sort=False):
        blocks.append('<div class="clip-recipient">收件人：' + html.escape(recipient) + '</div>')
        for (book, kind, status), items in recipient_orders.groupby(['book_name', 'stock_type', 'status'], sort=False, dropna=False):
            ids = '、'.join('#' + str(int(i)) for i in items['id'])
            label = f'【{kind}】{book} ×{len(items)}'
            if status != '已到货':
                label += f'（{status}）'
            blocks.append('<div class="clip-book">' + html.escape(label) + '<br><small>' + html.escape(ids) + '</small></div>')
    return ''.join(blocks)


def render_dispatch_details(group, buyer):
    scope = json.dumps([str(buyer), [int(i) for i in group['id']]], ensure_ascii=False)
    key = hashlib.sha256(scope.encode()).hexdigest()[:16]
    display = group[['id','book_name','stock_type','shop_name','status','recipient_name','shipping_notified_at','postage_paid_at','packed_at','packing_ref','allocation_note']].rename(columns={'id':'订单号','book_name':'书名／版本','stock_type':'类型','shop_name':'店铺','status':'状态','recipient_name':'收件人姓名','shipping_notified_at':'通知时间','postage_paid_at':'补邮时间','packed_at':'打包时间','packing_ref':'打包标记','allocation_note':'调货备注'})
    st.dataframe(display, hide_index=True, use_container_width=True)
    chosen = st.multiselect('处理哪些书（默认当前卡片全部，可改选部分）', group['id'].tolist(), default=group['id'].tolist(), format_func=lambda i: dispatch_label(group.loc[group['id'].eq(i)].iloc[0]), key='clip_pick_'+key)
    selected = group[group['id'].isin(chosen)]
    action_label = st.selectbox('操作', ['填写收件人姓名','标记已通知','标记已补邮','确认已打包／重贴面单','发货','撤回通知','撤回补邮','撤回打包'], key='clip_action_'+key)
    action = {'填写收件人姓名':'recipient','标记已通知':'notify','标记已补邮':'pay','确认已打包／重贴面单':'pack','发货':'take','撤回通知':'unnotify','撤回补邮':'unpay','撤回打包':'unpack'}[action_label]
    value = ''
    if action == 'recipient':
        recipients = selected['recipient_name'].fillna('').str.strip().unique().tolist()
        recipient_scope = hashlib.sha256(json.dumps([int(i) for i in chosen]).encode()).hexdigest()[:12]
        value = st.text_input('收件人姓名', value=recipients[0] if len(recipients) == 1 else '', key='clip_recipient_'+key+'_'+recipient_scope)
        st.caption('修改收件人后需重新确认打包及面单。')
    elif action == 'pack':
        value = st.text_input('打包标记／袋号（可留空）', key='clip_packing_'+key)
    if st.button('保存：'+action_label, disabled=not chosen or (action=='recipient' and not value.strip()), key='clip_save_'+key):
        dispatch_call('dispatch_update', {'p_expected':selected['_dispatch_snapshot'].tolist(), 'p_action':action, 'p_value':value.strip(), 'p_reason':action_label})


def package_summary(pkg_df):
    """Human-readable package contents for the package-management page."""
    books = []
    for book, n in pkg_df.groupby("book_name").size().items():
        books.append(f"{book} ×{int(n)}")
    buyers = [str(x) for x in pkg_df.get("buyer_name", pd.Series(dtype=str)).dropna().unique() if str(x).strip()]
    return "；".join(books), "、".join(buyers), len(pkg_df)


def package_stage(pkg_df):
    statuses = set(str(x) for x in pkg_df.get("status", []).tolist())
    if "在途" in statuses:
        return "🚚 在途"
    if statuses & {"官方已发货", "已合包", "已到达我方仓库"}:
        return "💰 待付运费"
    if statuses and statuses <= {"已到货"}:
        return "📦 已到货"
    return "⏳ 等待官方发货"



BUYER_ALIASES, BUYER_ALIASES_AVAILABLE = load_buyer_aliases()
if not BUYER_ALIASES_AVAILABLE and (st.secrets.get("BUYER_ALIAS_SERVICE_KEY", "") or st.session_state.get("buyer_alias_was_available")):
    st.error("別名資料暫時無法讀取。為免把已合併買家拆開發貨，請檢查別名表及金鑰後重試。")
    if st.button("重試讀取別名"):
        load_buyer_aliases.clear()
        st.rerun()
    st.stop()
if BUYER_ALIASES_AVAILABLE:
    st.session_state["buyer_alias_was_available"] = True
df = load_data()
DISPATCH_READY = df.attrs.get("dispatch_ready", False)
ORDER_FEATURES_READY = df.attrs.get("order_features_ready", False)
if not ORDER_FEATURES_READY:
    st.warning("請先啟用『通知發貨／下單平台』欄位，再使用這兩項新功能。")
    with st.expander("一次性設定：複製 SQL 到 Supabase SQL Editor 執行", expanded=True):
        st.code(ORDER_FEATURE_SQL, language="sql")
        if st.button("已執行 SQL，重新檢查", key="order_feature_refresh"):
            clear_cached_data()
            st.rerun()
if not df.empty:
    df["buyer_name_original"] = df["buyer_name"]
    df["buyer_name"] = df["buyer_name"].map(canonical_buyer)

# 五個主區取代十個平鋪的下拉選項；舊頁面實作保持不變。
NAV_GROUPS = {
    "🏠 首頁": ["🏠 工作台"],
    "✍️ 收單": ["📥 批量录入", "📝 常规录入"],
    "🛒 採購": ["🔮 预售", "📋 现货"],
    "📦 物流發貨": ["📦 包裹合拼与运费", "🚚 发货看板"],
    "⚙️ 更多": ["🔎 订单管理", "📚 书籍资料库", "📊 月度营收统计"],
}
NAV_LABELS = {
    "🏠 工作台": "今日待辦",
    "📥 批量录入": "同款多人／Excel 批量",
    "📝 常规录入": "單筆錄入",
    "🔮 预售": "預售採購",
    "📋 现货": "現貨採購",
    "📦 包裹合拼与运费": "付運費／在途／簽收",
    "🚚 发货看板": "登記發貨",
    "🔎 订单管理": "找單與修改",
    "📚 书籍资料库": "書籍資料庫",
    "📊 月度营收统计": "財務報表",
}

def group_for(page):
    for group_name, pages in NAV_GROUPS.items():
        if page in pages:
            return group_name
    return "🏠 首頁"

def navigate_to(page):
    """供首頁快捷按鈕使用；Streamlit 回調於 widget 建立前執行。"""
    group = group_for(page)
    st.session_state["nav_main"] = group
    st.session_state[f"nav_sub_{group}"] = page
    st.session_state["nav_selection"] = page

if "nav_selection" not in st.session_state:
    st.session_state["nav_selection"] = "🏠 工作台"
if st.session_state.pop("pending_redirect_t1", False):
    navigate_to("📝 常规录入")
if "nav_main" not in st.session_state:
    st.session_state["nav_main"] = group_for(st.session_state["nav_selection"])

st.caption("收單 → 官方採購 → 付運費／簽收 → 登記發貨")
main = st.radio(
    "主要功能", list(NAV_GROUPS), key="nav_main", horizontal=True,
    label_visibility="collapsed",
)
choices = NAV_GROUPS[main]
subkey = f"nav_sub_{main}"
if subkey not in st.session_state or st.session_state[subkey] not in choices:
    st.session_state[subkey] = choices[0]
if len(choices) > 1:
    selected_tab = st.radio(
        "選擇操作", choices, key=subkey, horizontal=True,
        format_func=lambda page: NAV_LABELS[page],
    )
else:
    selected_tab = choices[0]
st.session_state["nav_selection"] = selected_tab

# 原有功能區塊不需要改寫，維持既有訂單及 Supabase 操作。
tab0 = selected_tab == "🏠 工作台"
tab_batch = selected_tab == "📥 批量录入"
tab1 = selected_tab == "📝 常规录入"
tab2 = selected_tab == "📋 现货"
tab3 = selected_tab == "🔮 预售"
tab5 = selected_tab == "📦 包裹合拼与运费"
tab4 = selected_tab == "🚚 发货看板"
tab_orders = selected_tab == "🔎 订单管理"
tab_catalog = selected_tab == "📚 书籍资料库"
tab6 = selected_tab == "📊 月度营收统计"

# --- 操作工具：不修改现有 orders 表结构 ---
VERSIONS = ["普装", "官网特", "A店特", "特装"]

def money_split(total, n):
    """人民币按分分配，避免 100/3 在数据库出现尾差。"""
    cents = int((Decimal(str(total)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    base, extra = divmod(cents, n)
    return [(base + (1 if i < extra else 0)) / 100 for i in range(n)]

def name_and_version(full_name):
    match = re.match(r"^(.*?)[（(]([^（）()]*)[）)]$", str(full_name or ""))
    return (match.group(1).strip(), match.group(2).strip()) if match else (str(full_name or ""), "普装")

def presale_name_and_edition(full_name):
    """Only strip recognized edition suffixes; preserve subtitles in parentheses."""
    raw = str(full_name or "").strip()
    base, edition = name_and_version(raw)
    aliases = {"普裝": "普装", "特裝": "特装", "官網特": "官网特"}
    edition = aliases.get(edition, edition)
    if edition in VERSIONS:
        return base, edition
    return raw, "普装"


def presale_edition_counts(orders):
    presale = orders[orders["stock_type"].map(normalize_order_type).eq("预售")]
    return presale["book_name"].map(lambda name: presale_name_and_edition(name)[1]).value_counts().to_dict()


def presale_edition_label(counts):
    labels = {"普装": "普裝", "特装": "特裝", "官网特": "官網特", "A店特": "A店特"}
    return "、".join(f"{labels.get(edition, edition)} {counts[edition]} 本" for edition in VERSIONS if counts.get(edition, 0)) or "無"


def load_catalog():
    """书籍表为可选项；未创建时仍可从历史订单复用书名。"""
    try:
        response = supabase.table("book_catalog").select("id,book_name,edition,stock_type,default_price").order("book_name").limit(1000).execute()
        return pd.DataFrame(response.data or []), True
    except Exception:
        return pd.DataFrame(), False

def merge_catalog_entries(orders_df, catalog_df):
    found = set()
    records = []
    if not catalog_df.empty:
        for row in catalog_df.to_dict("records"):
            key = (str(row.get("book_name") or "").strip(), str(row.get("edition") or "普装"))
            if key[0] and key not in found:
                found.add(key)
                records.append({"书名": key[0], "版本": key[1], "来源": "书籍资料库"})
    if not orders_df.empty:
        for val in orders_df["book_name"].dropna().unique():
            key = name_and_version(val)
            if key[0] and key not in found:
                found.add(key)
                records.append({"书名": key[0], "版本": key[1], "来源": "历史订单"})
    return pd.DataFrame(records)

if tab0:
    st.subheader("🏠 訂單工作台｜收單 → 採購 → 付運費 → 登記發貨")
    if df.empty:
        st.info("目前尚無訂單。請到『收單』開始錄入。")
    else:
        def count_where(status=None, kind=None):
            mask = pd.Series(True, index=df.index)
            if status is not None:
                mask &= df["status"].isin(status if isinstance(status, list) else [status])
            if kind is not None:
                mask &= df["stock_type"].eq(kind)
            return int(mask.sum())
        a,b,c,d = st.columns(4)
        a.metric("🛒 待采购", count_where("买家已下单"))
        b.metric("📮 待官方发货", count_where("我方已下单"))
        c.metric("🚚 在途", count_where("在途"))
        d.metric("📦 可安排发货", count_where(["已到货"]))
        # 預售本來可能要等待多月；15 天預設日期不能直接當作預售逾期警報。
        deadline_df = df[
            ~df["status"].isin(FINISHED_STATUSES)
            & df["stock_type"].map(normalize_order_type).eq("现货")
        ].copy()
        deadline_df["剩余天数"] = (pd.to_datetime(deadline_df["deadline"], errors="coerce").dt.normalize() - pd.Timestamp.today().normalize()).dt.days
        urgent = deadline_df[deadline_df["剩余天数"].notna() & (deadline_df["剩余天数"] <= 5)].sort_values("剩余天数")
        st.markdown("#### ⏰ 現貨急需處理（5 天內截止或逾期）")
        st.caption("預售只計算已等待天數，不再因預設 15 天期限被列作逾期。")
        if urgent.empty:
            st.success("目前沒有接近截止日期的現貨訂單。")
        else:
            st.dataframe(urgent[["id", "buyer_name", "book_name", "status", "剩余天数"]].rename(columns={"id":"編號","buyer_name":"買家","book_name":"書籍","status":"狀態","剩余天数":"距截止日（天）"}).head(80), use_container_width=True, hide_index=True)
        st.markdown("#### ⚡ 直接開始處理")
        quick_actions = [
            ("➕ 同款多人收單", "📥 批量录入"),
            ("🛒 預售採購", "🔮 预售"),
            ("📋 現貨採購", "📋 现货"),
            ("💰 付運費／簽收", "📦 包裹合拼与运费"),
            ("🚚 登記發貨", "🚚 发货看板"),
            ("🔎 找單／修改", "🔎 订单管理"),
        ]
        for start in range(0, len(quick_actions), 3):
            cols = st.columns(3)
            for col, (label, page) in zip(cols, quick_actions[start:start+3]):
                col.button(label, key=f"go_{page}", use_container_width=True,
                           on_click=navigate_to, args=(page,))

if tab_batch:
    st.subheader("📥 多买家批量录入")
    st.caption("適合多位買家購買相同書款：每一行是一位買家的某款書。可直接從 Excel 複製貼上；匯入前先預覽確認。")
    with st.expander("⚡ 同款多人錄入（推薦）", expanded=True):
        st.caption("先選一次書款，再貼上不同買家；每行格式：買家名稱 | 數量 | 每本售價（RMB） | 下單日期時間。例如：小明 | 2 | 68 | 2026-10-14 09:15")
        with st.form("same_book_many_buyers"):
            entries = merge_catalog_entries(df, load_catalog()[0])
            names = sorted(entries["书名"].dropna().astype(str).unique()) if not entries.empty else []
            selected_book = st.selectbox("常用書名", ["-- 輸入新書名 --"] + names)
            manual_book = st.text_input("新書名", disabled=(selected_book != "-- 輸入新書名 --"))
            c1, c2, c3 = st.columns(3)
            with c1: same_edition = st.selectbox("版本", VERSIONS, key="same_ed")
            with c2: same_kind = st.selectbox("類型", ["现货", "预售"], key="same_kind")
            with c3: same_shop = st.selectbox("店鋪", SHOPS, key="same_shop")
            same_buyers = st.text_area("買家 | 數量 | 每本售價 | 下單時間", height=155, placeholder="小明 | 2 | 68 | 2026-10-14 09:15\n小紅 | 1 | 68 | 2026-10-14 10:32\n小華 | 3 | 65 | 2026-10-14 11:08")
            st.caption("每本售價為該買家單本實收金額；不同買家可不同價。")
            submitted_same = st.form_submit_button("📋 檢查並預覽", use_container_width=True)
        if submitted_same:
            try:
                book_name = (selected_book if selected_book != "-- 輸入新書名 --" else manual_book).strip()
                if not book_name: raise ValueError("請選擇或輸入書名")
                rows = []
                for lineno, line in enumerate(same_buyers.splitlines(), 1):
                    if not line.strip(): continue
                    cols = [x.strip() for x in (line.split("\t") if "\t" in line else line.split("|"))]
                    if len(cols) != 4: raise ValueError(f"第 {lineno} 行應有四欄：買家 | 數量 | 每本售價 | YYYY-MM-DD HH:MM")
                    buyer, count, price, ordered_at = cols
                    try:
                        order_dt = datetime.datetime.strptime(ordered_at, "%Y-%m-%d %H:%M")
                    except ValueError:
                        raise ValueError(f"第 {lineno} 行下單時間格式錯誤，應為 YYYY-MM-DD HH:MM")
                    if order_dt > datetime.datetime.now() + datetime.timedelta(minutes=2):
                        raise ValueError(f"第 {lineno} 行下單時間不可是未來")
                    if not buyer: raise ValueError(f"第 {lineno} 行買家不能空白")
                    qty = int(count)
                    val = Decimal(price)
                    if qty < 1 or qty > 100 or not val.is_finite() or val < 0 or val.as_tuple().exponent < -2:
                        raise ValueError(f"第 {lineno} 行數量或售價不正確")
                    for _ in range(qty):
                        rows.append({"buyer_name": canonical_buyer(buyer), "book_name": f"{book_name}（{same_edition}）", "shop_name": same_shop, "stock_type": same_kind, "status": "买家已下单", "order_time": order_dt.isoformat(), "deadline": (order_dt.date()+datetime.timedelta(days=15)).isoformat(), "price_sell": float(val), "price_buy": 0.0, "purchase_type": "合并拼单"})
                    if len(rows) > 300: raise ValueError("每次最多 300 本，請拆批")
                if not rows: raise ValueError("請輸入至少一位買家")
                st.session_state["same_book_preview"] = rows
                st.session_state["same_book_fingerprint"] = hashlib.sha256(repr(rows).encode()).hexdigest()
            except Exception as exc:
                st.session_state.pop("same_book_preview", None)
                st.error(f"無法預覽：{exc}")
        prepared = st.session_state.get("same_book_preview", [])
        if prepared:
            same_preview = pd.DataFrame(prepared)
            st.success(f"可保存：{same_preview['buyer_name'].nunique()} 位買家，共 {len(prepared)} 本，收款 ¥{same_preview['price_sell'].sum():.2f}")
            st.dataframe(same_preview.groupby(["buyer_name", "book_name", "order_time"],as_index=False).agg(本數=("price_sell","size"),應收=("price_sell","sum")).sort_values(["order_time", "buyer_name"]).rename(columns={"buyer_name":"買家", "book_name":"書款", "order_time":"下單時間"}), hide_index=True, use_container_width=True)
            token = st.session_state.get("same_book_fingerprint")
            duplicate = st.session_state.get("same_book_last_saved") == token
            if duplicate: st.warning("這批資料已保存，請勿重複提交。")
            if st.button("✅ 確認保存同款多人訂單", type="primary", disabled=duplicate, use_container_width=True):
                try:
                    # 單請求提交，避免多個小批次部分成功時無法判斷。
                    insert_buyer_orders(prepared)
                    st.session_state["same_book_last_saved"] = token
                    st.session_state.pop("same_book_preview", None)
                    clear_cached_data()
                    st.success(f"成功保存 {len(prepared)} 本，請前往訂單管理核對。")
                except Exception as exc:
                    st.error(f"保存失敗；請先檢查訂單列表，避免重試造成重複：{exc}")
    st.divider()
    st.markdown("#### 📥 通用 Excel 批量錄入（多款書、多位買家）")
    sample = """买家账号\t书名\t版本\t数量\t订单总收款\t店铺\t商品属性\t下单日期\t下单时间\t批次号\t每本售价
小明\t示例书A\t普装\t2\t120\t大号\t现货\t2026-10-13\t09:15\tA001\t
小明\t示例书B\t特装\t1\t120\t大号\t现货\t2026-10-13\t09:15\tA001\t
小红\t示例书C\t普装\t1\t68\t小号\t预售\t2026-10-13\t11:20\tA002\t"""
    st.download_button("📄 下载 Excel 可用的示范 TSV", data=sample.encode("utf-8-sig"), file_name="orders_batch_template.tsv", mime="text/tab-separated-values")
    upload = st.file_uploader("上载 CSV / TSV 文件", type=["csv", "tsv", "txt"], key="batch_file")
    pasted = st.text_area("或直接贴上 Excel 表格", height=155, key="batch_text")
    with st.expander("欄位使用說明", expanded=False):
        st.write("必填：买家账号、书名、数量、下单日期（YYYY-MM-DD）、下单时间（HH:MM）。可选：版本（默认普装）、订单总收款、店铺、商品属性、批次号、每本售价。")
        st.write("同一买家的同一批次号视为一张订单；同张订单的『订单总收款』每行须填写相同数字，系统按总本数分摊。如填写『每本售价』，则按逐行单价计算，忽略订单总收款；同一批次不能混用。")
    if upload and pasted.strip():
        st.caption("已上传文件时，系统优先读取上传的文件，忽略下方贴上的内容。")
    raw = upload.getvalue().decode("utf-8-sig") if upload else pasted
    if raw.strip():
        try:
            delimiter = "\t" if "\t" in raw.splitlines()[0] else ","
            incoming = pd.read_csv(io.StringIO(raw), sep=delimiter, dtype=str, keep_default_na=False).fillna("")
            incoming.columns = [str(c).strip() for c in incoming.columns]
            required = ["买家账号", "书名", "数量"]
            missing = [x for x in required if x not in incoming.columns]
            if missing: raise ValueError("缺少必要栏目：" + "、".join(missing))
            if len(incoming) > 1000: raise ValueError("每批最多 1000 行，请分批上传")
            items = []
            errors = []
            for idx, rec in incoming.iterrows():
                line=int(idx)+2
                try:
                    buyer = str(rec["买家账号"]).strip(); book = str(rec["书名"]).strip()
                    if not buyer or not book: raise ValueError("买家或书名为空")
                    qty = int(str(rec["数量"]).strip())
                    if qty < 1 or qty > 100: raise ValueError("数量必须在 1 至 100 之间")
                    edition = str(rec.get("版本", "") or "普装").strip()
                    if edition not in VERSIONS: raise ValueError("版本不合法")
                    shop = str(rec.get("店铺", "") or "大号").strip()
                    kind = str(rec.get("商品属性", "") or "现货").strip()
                    if shop not in SHOPS or kind not in ["现货","预售"]: raise ValueError("店铺或商品属性不合法")
                    order_day = str(rec.get("下单日期", "") or "").strip()
                    clock = str(rec.get("下单时间", "") or "").strip()
                    try:
                        ordered_dt = datetime.datetime.strptime(f"{order_day} {clock}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        raise ValueError("下单日期 / 下单时间必须分别填写 YYYY-MM-DD 和 HH:MM")
                    if ordered_dt > datetime.datetime.now() + datetime.timedelta(minutes=2):
                        raise ValueError("下单时间不可是未来")
                    order_day = ordered_dt.date().isoformat()
                    order_timestamp = ordered_dt.isoformat()
                    batch_no = str(rec.get("批次号", "") or f"行{line}").strip()
                    total = str(rec.get("订单总收款", "") or "").strip()
                    unit = str(rec.get("每本售价", "") or "").strip()
                    if total and (not Decimal(total).is_finite() or Decimal(total) < 0): raise ValueError("总收款不能为负数")
                    if unit and (not Decimal(unit).is_finite() or Decimal(unit) < 0): raise ValueError("单价不能为负数")
                    if unit and Decimal(unit).as_tuple().exponent < -2: raise ValueError("单价最多两位小数")
                    if total and Decimal(total).as_tuple().exponent < -2: raise ValueError("总额最多两位小数")
                    items.append({"buyer":buyer,"book":book,"edition":edition,"qty":qty,"shop":shop,"kind":kind,"day":order_day,"timestamp":order_timestamp,"batch":batch_no,"total":total,"unit":unit})
                except Exception as exc: errors.append(f"第 {line} 行：{exc}")
            groups={}
            for item in items:
                key=(item["buyer"],item["batch"])
                groups.setdefault(key,[]).append(item)
            output=[]
            for key, rows in groups.items():
                if len({(r["shop"],r["kind"],r["timestamp"]) for r in rows}) != 1:
                    errors.append(f"买家 {key[0]} 批次 {key[1]}：店铺/属性/下单日期时间不一致"); continue
                if any(r["unit"] for r in rows) and not all(r["unit"] for r in rows):
                    errors.append(f"买家 {key[0]} 批次 {key[1]}：不能部分填写每本售价"); continue
                amounts=set(r["total"] for r in rows if r["total"])
                if len(amounts)>1:
                    errors.append(f"买家 {key[0]} 批次 {key[1]}：订单总收款不一致"); continue
                if not all(r["unit"] for r in rows) and not amounts:
                    errors.append(f"买家 {key[0]} 批次 {key[1]}：未填写订单总收款或每本售价"); continue
                count=sum(r["qty"] for r in rows)
                if count>300:
                    errors.append(f"买家 {key[0]} 批次 {key[1]}：最多 300 本"); continue
                prices=([float(Decimal(r["unit"])) for r in rows for _ in range(r["qty"])] if all(r["unit"] for r in rows) else money_split(next(iter(amounts)), count))
                pos=0
                for r in rows:
                    for _ in range(r["qty"]):
                        output.append({"buyer_name":canonical_buyer(r["buyer"]), "book_name":f'{r["book"]}（{r["edition"]}）', "shop_name":r["shop"], "stock_type":r["kind"],"status":"买家已下单", "order_time":r["timestamp"], "deadline":(datetime.date.fromisoformat(r["day"])+datetime.timedelta(days=15)).isoformat(),"price_sell":prices[pos],"price_buy":0.0,"purchase_type":"合并拼单"})
                        pos+=1
            if errors:
                st.error("请修正以下问题后重新导入：\n" + "\n".join(errors[:20]))
            elif not output:
                st.warning("没有可录入的订单。")
            else:
                preview=pd.DataFrame(output)
                st.success(f"校验成功：{len(groups)} 个订单批次、{len(output)} 本书、收款 ¥{preview['price_sell'].sum():.2f}")
                st.dataframe(preview[["buyer_name","book_name","order_time","shop_name","stock_type","price_sell"]].sort_values("order_time").head(100).rename(columns={"buyer_name":"买家","book_name":"书籍","order_time":"下單時間","shop_name":"店铺","stock_type":"类型","price_sell":"单本售价"}),hide_index=True,use_container_width=True)
                st.caption("预览最多显示 100 行；实际提交为全部校验通过的资料。")
                fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                previous_success = st.session_state.get("batch_last_success") == fingerprint
                if previous_success:
                    st.warning("这一份文件在当前会话中已经成功提交，已禁止重复保存；如需重新导入，请先检查订单管理。")
                confirm=st.checkbox("我已核对买家、书名、数量及金额，确认新增这些订单", key="batch_confirm")
                if st.button("💾 确认批量保存", type="primary", disabled=(not confirm or previous_success)):
                    saved = 0
                    try:
                        # 分段写入，避免大批量请求超过网关限制；不声称跨批次事务原子性。
                        for start in range(0,len(output),100):
                            insert_buyer_orders(output[start:start+100])
                            saved+=len(output[start:start+100])
                        st.session_state["batch_last_success"] = fingerprint
                        clear_cached_data(); st.success(f"✅ 成功新增 {saved} 本。请勿重复提交同一份资料。")
                    except Exception as exc:
                        clear_cached_data(); st.error(f"保存中断：已确认成功写入前 {saved} 本；其余不确定，请先到订单管理核对，勿直接整批重试。错误：{exc}")
        except Exception as exc:
            st.error(f"无法解析资料：{exc}")

if tab_orders:
    st.subheader("🔎 搜尋與編輯訂單")
    render_buyer_alias_manager(df)
    if df.empty:
        st.info("目前沒有可查詢的訂單。")
    else:
        q=st.text_input("搜尋買家、書名或包裹編號", placeholder="輸入關鍵字")
        c1,c2,c3=st.columns(3)
        with c1: sf=st.selectbox("訂單狀態",["全部"]+STATUSES, key="manage_status")
        with c2: kf=st.selectbox("類型",["全部","现货","预售"], key="manage_kind")
        with c3: page=st.number_input("結果頁碼", min_value=1,value=1,step=1,key="manage_page")
        filtered=df.copy()
        if q:
            needle=re.escape(q.strip())
            filtered=filtered[filtered[["buyer_name","buyer_name_original","book_name","package_id"]].fillna("").astype(str).apply(lambda col:col.str.contains(needle,case=False,regex=True)).any(axis=1)]
        if sf!="全部": filtered=filtered[filtered["status"].eq(sf)]
        if kf!="全部": filtered=filtered[filtered["stock_type"].eq(kf)]
        filtered=filtered.sort_values("order_time", ascending=False)
        count=len(filtered); pages=max(1,(count+49)//50)
        st.caption(f"找到 {count} 筆，每頁顯示 50 筆，共 {pages} 頁")
        shown=filtered.iloc[(int(page)-1)*50:int(page)*50]
        if not shown.empty:
            st.dataframe(shown[["id","buyer_name","book_name","status","price_sell","price_buy","purchase_vendor","package_id","order_time"]].rename(columns={"id":"編號","buyer_name":"買家","book_name":"書籍","status":"狀態","price_sell":"售價 RMB","price_buy":"採購價 HKD","purchase_vendor":"下單平台","package_id":"包裹","order_time":"下單時間"}), use_container_width=True,hide_index=True)
            chosen=st.selectbox("選擇要修改的訂單編號",shown["id"].astype(int).tolist(),format_func=lambda x:f'#{x} · '+str(shown.loc[shown["id"].eq(x),"buyer_name"].iloc[0])+' · '+str(shown.loc[shown["id"].eq(x),"book_name"].iloc[0]))
            selected=shown.loc[shown["id"].eq(chosen)].iloc[0]
            with st.form(f"edit_order_{chosen}"):
                ea,eb=st.columns(2)
                with ea:
                    ebuyer=st.text_input("買家帳號",value=str(selected["buyer_name"] or ""))
                    ebook=st.text_input("書名（包含版本）",value=str(selected["book_name"] or ""))
                    estat=st.selectbox("狀態",STATUSES,index=STATUSES.index(selected["status"]) if selected["status"] in STATUSES else 0)
                with eb:
                    eshop=st.selectbox("店鋪",SHOPS,index=SHOPS.index(selected["shop_name"]) if selected["shop_name"] in SHOPS else 0)
                    ekind=st.selectbox("商品類型",["现货","预售"],index=0 if selected["stock_type"]=="现货" else 1)
                    eprice=st.number_input("售價 RMB",min_value=0.0,value=float(selected["price_sell"]),format="%.2f")
                    ecost=st.number_input("採購成本 HKD",min_value=0.0,value=float(selected["price_buy"]),format="%.2f")
                    vendor_options = [""] + PURCHASE_VENDORS
                    current_vendor = str(selected.get("purchase_vendor") or "")
                    if current_vendor not in vendor_options:
                        vendor_options.append(current_vendor)
                    evendor = st.selectbox("下單平台", vendor_options, index=vendor_options.index(current_vendor), format_func=lambda x: x or "未記錄", disabled=not ORDER_FEATURES_READY)
                update=st.form_submit_button("💾 保存修改",type="primary", disabled=pd.notna(selected.get("taken_at")))
            if update:
                if estat in FINISHED_STATUSES and selected["status"] not in FINISHED_STATUSES:
                    st.error("请在发货看板登记已带走，以核对收件人及补邮记录。")
                elif not ebuyer.strip() or not ebook.strip(): st.error("買家及書名不可留空")
                else:
                    try:
                        supabase.table("orders").update({"buyer_name":canonical_buyer(ebuyer),"book_name":ebook.strip(),"status":estat,"shop_name":eshop,"stock_type":ekind,"price_sell":eprice,"price_buy":ecost, **({"purchase_vendor": evendor} if ORDER_FEATURES_READY else {})}).eq("id",int(chosen)).execute()
                        clear_cached_data(); st.success("修改成功"); st.rerun()
                    except Exception as exc: st.error(f"修改失敗：{exc}")
            with st.expander("⚠️ 刪除錯誤錄入的訂單"):
                st.warning("刪除後不可在本頁恢復。只有確認資料錄錯才使用。")
                if st.checkbox(f"確認永久刪除 #{chosen}",key=f"del_ok_{chosen}"):
                    if st.button("刪除此筆訂單",key=f"del_{chosen}", disabled=pd.notna(selected.get("taken_at"))):
                        try:
                            supabase.table("orders").delete().eq("id",int(chosen)).execute()
                            clear_cached_data();st.success("已刪除");st.rerun()
                        except Exception as exc: st.error(f"刪除失敗：{exc}")

if tab_catalog:
    st.subheader("📚 書款資料庫｜同款不同買家可重複使用")
    catalog, available=load_catalog()
    entries=merge_catalog_entries(df,catalog)
    if not entries.empty:
        st.dataframe(entries,hide_index=True,use_container_width=True)
    else: st.info("目前沒有歷史書籍。")
    if not available:
        st.warning("尚未建立 book_catalog 表。上方只從歷史訂單讀取書名；要新增書籍並永久保存，請先執行隨附 SQL 建表檔。")
    else:
        with st.form("catalog_new",clear_on_submit=True):
            n=st.text_input("新增書名")
            a,b,c=st.columns(3)
            with a: v=st.selectbox("版本",VERSIONS)
            with b: kind=st.selectbox("類型",["现货","预售"])
            with c: default_price=st.number_input("建議售價 RMB",min_value=0.0,format="%.2f")
            add=st.form_submit_button("➕ 儲存書籍",type="primary")
        if add:
            if not n.strip(): st.error("請輸入書名")
            else:
                try:
                    supabase.table("book_catalog").upsert({"book_name":n.strip(),"edition":v,"stock_type":kind,"default_price":default_price},on_conflict="book_name,edition").execute()
                    st.success("已儲存書籍資料");st.rerun()
                except Exception as exc:st.error(f"儲存失敗：{exc}")
        if not catalog.empty:
            with st.expander("編輯或刪除書籍資料"):
                selected_id=st.selectbox("選擇書籍",catalog["id"].astype(int).tolist(),format_func=lambda x: str(catalog.loc[catalog["id"].eq(x),"book_name"].iloc[0])+" · "+str(catalog.loc[catalog["id"].eq(x),"edition"].iloc[0]))
                cr=catalog.loc[catalog["id"].eq(selected_id)].iloc[0]
                with st.form("catalog_edit"):
                    edit_name=st.text_input("書名",str(cr["book_name"]))
                    edit_ed=st.selectbox("版本",VERSIONS,index=VERSIONS.index(cr["edition"]) if cr["edition"] in VERSIONS else 0)
                    edit_price=st.number_input("建議售價",min_value=0.0,value=float(cr["default_price"]) if pd.notna(cr["default_price"]) else 0.0,format="%.2f")
                    save_catalog=st.form_submit_button("保存書籍修改")
                if save_catalog:
                    try:
                        supabase.table("book_catalog").update({"book_name":edit_name.strip(),"edition":edit_ed,"default_price":edit_price}).eq("id",int(selected_id)).execute()
                        st.success("已更新");st.rerun()
                    except Exception as exc:st.error(str(exc))
                if st.checkbox("確認刪除這款書的資料（不影響歷史訂單）"):
                    if st.button("刪除書籍資料"):
                        try: supabase.table("book_catalog").delete().eq("id",int(selected_id)).execute();st.rerun()
                        except Exception as exc:st.error(str(exc))

# ==================== TAB 1: 快速 / 常规录入 ====================
if tab1:
    st.markdown("### 📝 订单录入")
    st.caption("极速录入：一行一个书款，支持同款多本；普通录入：保留原来的逐本选择。")

    # 只在成功提交后的下一次重绘清空输入，避免更改有状态的组件时报错。
    if st.session_state.pop("should_clear_t1", False):
        st.session_state["t1_price_editable"] = 0.0
        st.session_state["t1_quick_books"] = ""
        if not st.session_state.get("t1_keep_buyer", False):
            st.session_state["t1_buyer"] = ""

    for k, val in [("t1_buyer", ""), ("t1_price_editable", 0.0), ("t1_quick_books", "")]:
        if k not in st.session_state:
            st.session_state[k] = val

    existing_books = []
    # 同一款書常由不同買家購買，只復用書名，不再處理預售日期。
    if not df.empty and "book_name" in df.columns:
        for raw in df["book_name"].dropna().astype(str).unique():
            base, _ = name_and_version(raw)
            if base:
                existing_books.append(base)
    catalog_df, catalog_ok = load_catalog()
    if catalog_ok and not catalog_df.empty:
        existing_books.extend(str(v).strip() for v in catalog_df["book_name"].dropna() if str(v).strip())
    existing_books = sorted(set(existing_books))

    entry_mode = st.radio("录入方式", ["⚡ 极速录入", "📚 逐本录入"], horizontal=True, key="t1_mode")
    with st.form("t1_order_form", clear_on_submit=False):
        a, b = st.columns(2)
        with a:
            buyer = st.text_input("买家账号 *", key="t1_buyer")
            p_sell_total = st.number_input("买家付款总额 (RMB ¥)", min_value=0.0, step=1.0, format="%.2f", key="t1_price_editable")
            shop = st.selectbox("下单店铺", SHOPS, key="t1_shop")
            keep_buyer = st.checkbox("保存後保留買家名稱（可選）", key="t1_keep_buyer")
        with b:
            stock_type = st.radio("商品属性", ["现货", "预售"], horizontal=True, key="t1_stock_type")
            status = st.selectbox("当前订单状态", STATUSES, key="t1_status")
            input_date = st.date_input("买家下单日期", value=datetime.date.today(), key="t1_date")
            input_time = st.time_input("买家下单时间", value=datetime.datetime.now().time().replace(second=0, microsecond=0), key="t1_time")
        auto_deadline = input_date + datetime.timedelta(days=15)
        if stock_type == "现货":
            st.caption(f"現貨截止日期：{auto_deadline:%Y-%m-%d}（沿用原有 15 天規則）")
        else:
            st.caption("預售：不按預設 15 天期限判定逾期，待發期間顯示實際已等待天數。")

        book_entries = []
        parse_error = ""
        if entry_mode == "⚡ 极速录入":
            st.markdown("**一行一个书款：`书名 | 版本 | 数量`**")
            st.caption("例如：某某小说 | 普装 | 2。版本可以省略（默认普装），数量可以省略（默认 1）。可从表格整批粘贴，Tab 分隔也支持。")
            quick_text = st.text_area("书籍清单", height=170, key="t1_quick_books", placeholder="某某小说 | 普装 | 2\n另一本书 | 官网特 | 1")
            for i, line in enumerate(quick_text.splitlines(), 1):
                if not line.strip():
                    continue
                parts = [x.strip() for x in (line.split("\t") if "\t" in line else line.split("|"))]
                if len(parts) > 3 or not parts[0]:
                    parse_error = f"第 {i} 行格式不正确：请使用 书名 | 版本 | 数量"
                    break
                name = parts[0]
                edition = "普装"
                quantity = 1
                if len(parts) >= 2 and parts[1]:
                    if parts[1].isdigit() and len(parts) == 2:
                        quantity = int(parts[1])
                    else:
                        edition = parts[1]
                if len(parts) == 3 and parts[2]:
                    try:
                        quantity = int(parts[2])
                    except ValueError:
                        parse_error = f"第 {i} 行数量不是整数"
                        break
                if edition not in ["官网特", "A店特", "特装", "普装"]:
                    parse_error = f"第 {i} 行版本无效：{edition}"
                    break
                if quantity < 1 or quantity > 100:
                    parse_error = f"第 {i} 行数量须为 1–100"
                    break
                book_entries.extend([{"name": name, "edition": edition}] * quantity)
                if len(book_entries) > 300:
                    parse_error = "单次录入不能超过 300 本，请分批提交"
                    break
            if book_entries and not parse_error:
                st.caption(f"已识别 {len(book_entries)} 本书、{len([line for line in quick_text.splitlines() if line.strip()])} 行")
        else:
            num_books = st.number_input("本次订单包含几本书？", min_value=1, max_value=20, value=1, step=1)
            for i in range(int(num_books)):
                st.markdown(f"**第 {i + 1} 本书**")
                bc1, bc2, bc3 = st.columns([2, 2, 1])
                with bc1:
                    sel_hist = st.selectbox("历史书名", ["-- 手动输入新书名 --"] + existing_books, key=f"t1_hist_{i}")
                with bc2:
                    man_book = st.text_input("新书名", key=f"t1_man_{i}", disabled=(sel_hist != "-- 手动输入新书名 --"))
                with bc3:
                    edition = st.selectbox("版本", ["官网特", "A店特", "特装", "普装"], index=3, key=f"t1_ed_{i}")
                real_name = sel_hist if sel_hist != "-- 手动输入新书名 --" else man_book.strip()
                if real_name:
                    book_entries.append({"name": real_name, "edition": edition})

        if book_entries and not parse_error:
            st.info(f"📖 即将保存 **{len(book_entries)} 本**，总收款 **¥{p_sell_total:.2f}**；每本自动平均分摊。")
        submitted = st.form_submit_button("💾 保存本次订单", type="primary", use_container_width=True)

    if submitted:
        if not buyer.strip():
            st.error("请输入买家账号。")
        elif parse_error:
            st.error(parse_error)
        elif not book_entries:
            st.error("至少填写一本书。")
        else:
            split_prices = money_split(p_sell_total, len(book_entries))
            combined_datetime = datetime.datetime.combine(input_date, input_time).isoformat()
            order_rows = [{
                "buyer_name": canonical_buyer(buyer),
                "book_name": f"{item['name']}（{item['edition']}）",
                "shop_name": shop,
                "status": status,
                "price_sell": split_prices[i],
                "price_buy": 0.0,
                "purchase_type": "合并拼单",
                "order_time": combined_datetime,
                "stock_type": stock_type,
                "deadline": auto_deadline.isoformat(),
            } for i, item in enumerate(book_entries)]
            try:
                with st.spinner("正在保存订单..."):
                    insert_buyer_orders(order_rows)
                clear_cached_data()
                st.session_state["should_clear_t1"] = True
                st.toast(f"已保存 {len(book_entries)} 本书 · {buyer.strip()}", icon="✅")
                st.rerun()
            except Exception as exc:
                st.error(f"保存失败，输入内容已保留，可检查后重试：{exc}")

if tab2:
    st.markdown("### ⏳ 现货等待下单区")
    st.info("💡 显示所有属性为【现货】且状态为【买家已下单】的订单。在此多选并填写总成本后一键变更为【我方已下单】。")
    
    if not df.empty:
        selected_rows = pd.DataFrame()
        # 🎯 筛选：现货 + 买家已下单
        spot_wait_df = df[(df["stock_type"] == "现货") & (df["status"] == "买家已下单")].copy()
        
        if not spot_wait_df.empty:
            # 插入勾选列
            spot_wait_df.insert(0, "勾选下单", False)
            
            # 🔄 重新排序列：把书名 (book_name) 紧跟在勾选框后面，提到最前面！
            cols_order = [
                "勾选下单", 
                "book_name",     # 👈 书名排在第 2 位
                "id", 
                "buyer_name", 
                "price_sell", 
                "deadline", 
                "order_time"
            ]
            
            # 过滤出当前表里真实存在的列
            available_cols = [c for c in cols_order if c in spot_wait_df.columns]
            
            # 友好化表头名称
            display_df = spot_wait_df[available_cols].rename(columns={
                "book_name": "📖 书名",
                "id": "订单编号",
                "buyer_name": "买家账号",
                "price_sell": "买家下单价",
                "deadline": "发货截止日期",
                "order_time": "下单时间"
            })
            
            edited_spot = st.data_editor(
                display_df,
                column_config={
                    "勾选下单": st.column_config.CheckboxColumn("勾选打包", default=False)
                },
                disabled=["📖 书名", "订单编号", "买家账号", "买家下单价", "发货截止日期", "下单时间"],
                use_container_width=True,
                key="spot_wait_editor",
                hide_index=True  # 👈 隐藏最左侧自带的 0,1,2,3 行号，节省手机屏幕空间
            )
            
            selected_rows = edited_spot[edited_spot["勾选下单"] == True]
            
        if not selected_rows.empty:
                st.markdown(f"#### 🛒 已勾选 **{len(selected_rows)}** 个现货订单")
                
                with st.form("spot_batch_form"):
                    spot_vendor = st.selectbox("下單平台", PURCHASE_VENDORS, index=None, placeholder="選擇本批下單地方", key="spot_purchase_vendor")
                    spot_total_cost = st.number_input("这批勾选现货的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入供应商账单总价，系统会自动平摊到这些书的成本中")
                    
                    if st.form_submit_button("⚡ 确认现货已下单并平摊成本", type="primary", disabled=not ORDER_FEATURES_READY):
                        if not spot_vendor:
                            st.error("請先選擇下單平台。")
                            st.stop()
                        target_ids = selected_rows["订单编号"].tolist()
                        split_cost = spot_total_cost / len(target_ids) if len(target_ids) > 0 else 0.0
                        
                        # 新包裹号不再使用“最早下单时间”，改为“创建时间 + 短码”，避免同一分钟多个包裹撞号。
                        package_batch = make_package_id("现货", selected_rows)
                        
                        batch_update_orders(
                            target_ids,
                            {
                                "status": "我方已下单",
                                "price_buy": split_cost,
                                "purchase_vendor": spot_vendor,
                                "package_id": package_batch
                            }
                        )
                        clear_cached_data()
                            
                        books_now = "；".join(selected_rows["📖 书名"].astype(str).tolist())
                        st.success(
                            f"✅ 已下单！\n\n"
                            f"📦 包裹编号：**{package_batch}**\n\n"
                            f"📚 本次内容：**{books_now}**\n\n"
                            f"📦 共 {len(target_ids)} 本"
                        )
                        st.rerun()
        else:
            st.success("🎉 目前没有需要去下单的现货订单！")
    else:
        st.info("暂无数据。")


    # ==================== 现货第二阶段：等待官方发货 ====================
    st.write("---")
    st.subheader("📮 现货等待官方发货")
    st.info("💡 现货已经【我方已下单】后，卖家发货时在这里勾选；可按包裹一次性改成【官方已发货】。")

    spot_shipping_df = df[(df["stock_type"] == "现货") & (df["status"] == "我方已下单")].copy() if not df.empty else pd.DataFrame()

    if not spot_shipping_df.empty:
        # 有 package_id 的订单按包裹显示，避免把同一批订单拆散。
        grouped = []
        for package_id, g in spot_shipping_df.groupby("package_id", dropna=False):
            package_id = str(package_id) if pd.notna(package_id) and str(package_id).strip() else "未分包裹"
            grouped.append({
                "package_id": package_id,
                "下單平台": "、".join(dict.fromkeys(g["purchase_vendor"].replace("", "未記錄"))),
                "📚 本包书籍": "；".join(f"{b} ×{n}" for b, n in g.groupby("book_name").size().items()),
                "👤 买家": "、".join(str(x) for x in g["buyer_name"].dropna().unique() if str(x).strip()),
                "本包本数": len(g),
                "原始订单ids": list(g["id"]),
            })
        spot_ship_summary = pd.DataFrame(grouped)
        spot_ship_summary.insert(0, "☑️ 已发货", False)

        edited_spot_ship = st.data_editor(
            spot_ship_summary[["☑️ 已发货", "package_id", "下單平台", "本包本数", "📚 本包书籍", "👤 买家"]],
            column_config={
                "☑️ 已发货": st.column_config.CheckboxColumn("☑️ 勾选已发货", default=False),
                "本包本数": st.column_config.NumberColumn("本数", format="%d 本"),
            },
            disabled=["package_id", "下單平台", "本包本数", "📚 本包书籍", "👤 买家"],
            use_container_width=True,
            hide_index=True,
            key="spot_shipping_summary_editor",
        )

        selected_spot_ship = edited_spot_ship[edited_spot_ship["☑️ 已发货"] == True]
        if not selected_spot_ship.empty:
            all_spot_ship_ids = []
            for idx in selected_spot_ship.index:
                all_spot_ship_ids.extend(spot_ship_summary.loc[idx, "原始订单ids"])

            if st.button("🚚 批量标记为【官方已发货】", type="primary", key="btn_spot_confirm_shipping"):
                batch_update_orders(all_spot_ship_ids, {"status": "官方已发货"})
                clear_cached_data()
                st.success(f"✅ 已将 {len(all_spot_ship_ids)} 个现货订单标记为【官方已发货】。")
                st.rerun()
    else:
        st.success("🎉 目前没有等待官方发货的现货包裹！")


     # ====== TAB 3: 预售 ======
if tab3:
    st.markdown("### 🔮 预售")
    
    # ==================== 第一阶段：待下单 ====================
    st.subheader("🛒 第一阶段：待下单汇总")
    st.info("💡 如果因为限购等原因只能部分下单，可下拉修改【本次下单数量】，系统会按买家下单时间的【先来后到】优先分配！")
    
    if not df.empty:
        # 按 order_time 排序，保证优先分配给早下单的买家
        if "order_time" in df.columns:
            presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].sort_values(by="order_time").copy()
        else:
            presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].copy()
            
        if not presale_wait_df.empty:
            group_cols = ["book_name"]
            
            presale_summary = presale_wait_df.sort_values(["order_time", "id"]).groupby(group_cols, sort=False).agg(
                待下单数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            presale_summary = presale_summary.sort_values(by="待下单数量", ascending=False)
            
            presale_summary = presale_summary.rename(columns={
                "book_name": "📖 预售书名",
                "待下单数量": "🔥 待下单总数"
            })
            
            presale_summary.insert(0, "选择下单", False)
            presale_summary.insert(1, "本次下单数量", presale_summary["🔥 待下单总数"])
            
            # 🎯 核心优化：动态生成下拉菜单的最大选项数量
            max_possible_qty = int(presale_summary["🔥 待下单总数"].max())
            # 生成 [1, 2, 3, ... max] 的列表作为下拉选项
            qty_options = list(range(1, max_possible_qty + 1))
            
            cols_order = ["选择下单", "本次下单数量", "🔥 待下单总数", "📖 预售书名", "买家列表"]
            available_pre_cols = [c for c in cols_order if c in presale_summary.columns]
            
            edited_presale = st.data_editor(
                presale_summary[available_pre_cols],
                column_config={
                    "选择下单": st.column_config.CheckboxColumn("☑️ 确认下单", default=False),
                    # 🎯 核心修改：替换为下拉选择列
                    "本次下单数量": st.column_config.SelectboxColumn(
                        "🛒 下单数量 (下拉)", 
                        options=qty_options,
                        help="如遇到限购，请点击下拉修改本次实际买到的数量"
                    ),
                    "🔥 待下单总数": st.column_config.NumberColumn("🔥 待下单总数", disabled=True)
                },
                disabled=["🔥 待下单总数", "📖 预售书名", "买家列表"],
                use_container_width=True,
                key="presale_summary_editor"
            )
            
            selected_pre_rows = edited_presale[edited_presale["选择下单"] == True]
            
            if not selected_pre_rows.empty:
                st.markdown(f"#### 🎯 已勾选了 **{len(selected_pre_rows)}** 款预售书准备下单")
                
                with st.form("presale_batch_form"):
                    pre_vendor = st.selectbox("下單平台", PURCHASE_VENDORS, index=None, placeholder="選擇本批下單地方", key="pre_purchase_vendor")
                    pre_total_cost = st.number_input("这批勾选预售书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入总价，系统会自动平摊")
                    
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary", disabled=not ORDER_FEATURES_READY):
                        if not pre_vendor:
                            st.error("請先選擇下單平台。")
                            st.stop()
                        all_target_ids = []
                        
                        for _, row in selected_pre_rows.iterrows():
                            matched_idx = row.name
                            orig_ids = presale_summary.loc[matched_idx, "原始订单ids"]
                            
                            request_qty = int(row["本次下单数量"])
                            max_qty = len(orig_ids)
                            # 绝对安全的底层兜底：就算下拉不小心选多了，也按实际存在的上限扣
                            actual_qty = min(request_qty, max_qty) 
                            
                            target_ids = orig_ids[:actual_qty]
                            all_target_ids.extend(target_ids)
                        
                        split_pc = pre_total_cost / len(all_target_ids) if len(all_target_ids) > 0 else 0.0
                        
                        # 新包裹号不再使用“最早下单时间”，改为“创建时间 + 短码”。
                        target_orders_df = df[df["id"].isin(all_target_ids)]
                        package_batch = make_package_id("预售", target_orders_df)
                        
                        batch_update_orders(
                            all_target_ids,
                            {
                                "status": "我方已下单",
                                "price_buy": split_pc,
                                "purchase_vendor": pre_vendor,
                                "package_id": package_batch
                            }
                        )
                        clear_cached_data()
                            
                        books_now_df = df[df["id"].isin(all_target_ids)]
                        books_now = "；".join(books_now_df["book_name"].astype(str).tolist())
                        st.success(
                            f"✅ 采购完成！本次实际下单 **{len(all_target_ids)}** 本。\n\n"
                            f"📦 包裹编号：**{package_batch}**\n\n"
                            f"📚 本次内容：**{books_now}**\n\n"
                            f"未买够的订单会自动保留在上方。"
                        )
                        st.rerun()
        else:
            st.success("🎉 太棒了！当前没有任何等待下单的预售订单。")
            
        st.write("---")
      # ==================== 第二阶段：等待发货 ====================
        st.subheader("⏳ 第二阶段：等待官方发货汇总")
        st.info("📦 这里显示的是你【已经向官方下单】但还没发货的预售款。可依書款查看待處理數量。")
        
        # 🎯 筛选预售且状态为“我方已下单”的需求
        presale_shipping_df = df[(df["stock_type"] == "预售") & (df["status"] == "我方已下单")].copy()
        
        if not presale_shipping_df.empty:
            group_cols = ["book_name", "purchase_vendor"]
            shipping_summary = presale_shipping_df.groupby(group_cols).agg(
                等待发货数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            # 按发货时间排序，越早发货的排在越前面
            shipping_summary = shipping_summary.sort_values(by="等待发货数量", ascending=False)
            
            shipping_summary = shipping_summary.rename(columns={
                "book_name": "📖 预售书名",
                "purchase_vendor": "下單平台",
                "等待发货数量": "⏳ 苦等发货本数"
            })
            
            shipping_summary.insert(0, "标记已发货", False)
            cols_order_ship = ["标记已发货", "⏳ 苦等发货本数", "📖 预售书名", "下單平台", "买家列表"]
            available_ship_cols = [c for c in cols_order_ship if c in shipping_summary.columns]
            
            edited_shipping = st.data_editor(
                shipping_summary[available_ship_cols],
                column_config={
                    "标记已发货": st.column_config.CheckboxColumn("勾选已发货", default=False),
                    "⏳ 苦等发货本数": st.column_config.NumberColumn("⏳ 待发本数", format="%d 本")
                },
                disabled=["⏳ 苦等发货本数", "📖 预售书名", "下單平台", "买家列表"],
                use_container_width=True,
                key="shipping_summary_editor"
            )
            
            selected_shipping_rows = edited_shipping[edited_shipping["标记已发货"] == True]
            
            if not selected_shipping_rows.empty:
                st.markdown(f"#### 📦 已勾选 **{len(selected_shipping_rows)}** 款，官方终于发货啦！")
                
                if st.button("🚀 批量标记为【官方已发货】", type="primary", key="btn_confirm_shipping"):
                    all_ship_ids = []
                    for _, row in selected_shipping_rows.iterrows():
                        matched_idx = row.name
                        orig_ids = shipping_summary.loc[matched_idx, "原始订单ids"]
                        all_ship_ids.extend(orig_ids)
                        
                    batch_update_orders(all_ship_ids, {"status": "官方已发货"})
                    clear_cached_data()
                        
                    st.success(f"✅ 成功将勾选的预售书标记为【官方已发货】状态（共涉及 {len(all_ship_ids)} 个单子）！")
                    st.rerun()
        else:
            st.success("🎉 目前没有卡在等待官方发货阶段的预售书！")

# ====== TAB 4: 買家發貨登記 ======
if tab4:
    render_dispatch_panel(df)


# ====== TAB 5: 📦 包裹管理：待付运费 / 在途分开 ======
if tab5:
    st.markdown("### 📦 包裹管理")
    st.info("💡 现在分成两个区域：① 待付运费：只处理已经官方发货/合包的包裹；② 在途：只显示已经付过运费的包裹，到达后直接在这里签收。")

    if not df.empty and "package_id" in df.columns:
        pack_df = df[df["package_id"].notna() & (df["package_id"] != "")].copy()

        if pack_df.empty:
            st.success("🎉 目前没有任何已生成包裹号的订单。")
        else:
            # 按包裹聚合，给每个包裹生成“内容摘要”，用户不用再靠 PKG 时间猜里面是什么。
            package_groups = []
            for pkg_id, g in pack_df.groupby("package_id", sort=False):
                books_text, buyers_text, count = package_summary(g)
                package_groups.append({
                    "package_id": pkg_id,
                    "books": books_text,
                    "buyers": buyers_text,
                    "count": count,
                    "stage": package_stage(g),
                    "status_set": set(str(x) for x in g["status"].tolist()),
                })
            package_index = pd.DataFrame(package_groups)

            # =====================================================
            # A. 待付运费：只显示“官方已发货/已合包/已到达我方仓库”的包裹
            # =====================================================
            st.markdown("## 💰 A. 待付运费")
            st.caption("这些包裹已经进入可以付海外运费的阶段。付完后会自动移动到【🚚 在途】，以后签收不用再找这个包裹。")

            waiting_ids = package_index.loc[
                package_index["status_set"].apply(
                    lambda x: bool(x & {"官方已发货", "已合包", "已到达我方仓库"}) and "在途" not in x
                ),
                "package_id"
            ].tolist()

            if waiting_ids:
                for pkg_id in waiting_ids:
                    g = pack_df[pack_df["package_id"] == pkg_id].copy()
                    books_text, buyers_text, count = package_summary(g)
                    stage = package_stage(g)

                    with st.container(border=True):
                        st.markdown(f"### 📦 {pkg_id}")
                        c1, c2, c3 = st.columns([1, 2, 2])
                        with c1:
                            st.metric("本数", f"{count} 本")
                        with c2:
                            st.markdown(f"**📚 包裹内容**\n\n{books_text}")
                        with c3:
                            st.markdown(f"**👤 买家**\n\n{buyers_text or '—'}")

                        st.caption(f"状态：{stage}")
                        with st.expander("查看包裹内全部订单", expanded=False):
                            detail = g[[c for c in ["id", "buyer_name", "book_name", "status", "price_buy"] if c in g.columns]].rename(columns={
                                "id": "订单号", "buyer_name": "买家", "book_name": "📖 书名", "status": "状态", "price_buy": "采购价"
                            })
                            st.dataframe(detail, hide_index=True, use_container_width=True)

                        with st.form(f"freight_form_{pkg_id}"):
                            total_freight = st.number_input(
                                "💵 海外总邮费 (HK$)", min_value=0.0, format="%.2f",
                                key=f"freight_{pkg_id}"
                            )
                            if st.form_submit_button("⚡ 已付运费 → 转为【在途】", type="primary", use_container_width=True):
                                order_ids = g.loc[g["status"].isin(["官方已发货", "已合包", "已到达我方仓库"]) & g["taken_at"].isna(), "id"].tolist()
                                split_freight = total_freight / len(order_ids) if order_ids else 0.0
                                batch_update_orders(order_ids, {"shipping_fee": split_freight, "status": "在途"})
                                clear_cached_data()
                                st.success(f"✅ {pkg_id} 已进入【在途】。")
                                st.rerun()
            else:
                st.success("🎉 目前没有等待支付海外运费的包裹。")

            st.write("---")

            # =====================================================
            # B. 在途：只显示已经付过运费的包裹
            # =====================================================
            st.markdown("## 🚚 B. 在途")
            st.caption("这里就是你真正需要等待签收的包裹。到货后直接点对应包裹的【签收】，不用回上面重新找。")

            transit_ids = package_index.loc[
                package_index["status_set"].apply(lambda x: "在途" in x),
                "package_id"
            ].tolist()

            if transit_ids:
                for pkg_id in transit_ids:
                    g = pack_df[pack_df["package_id"] == pkg_id].copy()
                    books_text, buyers_text, count = package_summary(g)
                    freight = pd.to_numeric(g.get("shipping_fee", 0), errors="coerce").fillna(0).sum()

                    with st.container(border=True):
                        st.markdown(f"### 🚚 {pkg_id}")
                        c1, c2, c3 = st.columns([1, 2, 2])
                        with c1:
                            st.metric("本数", f"{count} 本")
                            st.metric("总邮费", f"HK$ {freight:.2f}")
                        with c2:
                            st.markdown(f"**📚 包裹内容**\n\n{books_text}")
                        with c3:
                            st.markdown(f"**👤 买家**\n\n{buyers_text or '—'}")

                        with st.expander("查看包裹内全部订单", expanded=False):
                            detail = g[[c for c in ["id", "buyer_name", "book_name", "status", "price_buy", "shipping_fee"] if c in g.columns]].rename(columns={
                                "id": "订单号", "buyer_name": "买家", "book_name": "📖 书名", "status": "状态", "price_buy": "采购价", "shipping_fee": "分摊邮费"
                            })
                            st.dataframe(detail, hide_index=True, use_container_width=True)

                        if st.button("📦 包裹已到达 → 一键签收", type="primary", use_container_width=True, key=f"arrive_{pkg_id}"):
                            batch_update_orders(g.loc[g["status"].eq("在途") & g["taken_at"].isna(), "id"].tolist(), {"status": "已到货"})
                            clear_cached_data()
                            st.success(f"✅ {pkg_id} 已签收！现在会自动从【在途】移除，并进入后续发货流程。")
                            st.rerun()
            else:
                st.success("📭 目前没有在途包裹。")

            # =====================================================
            # C. 历史包裹：已到货/已完结只作为折叠参考，不再混入待付运费和在途
            # =====================================================
            with st.expander("📚 查看历史已到货/已完结包裹", expanded=False):
                history_ids = package_index.loc[
                    package_index["status_set"].apply(lambda x: bool(x & {"已到货", "卖家已发货", "已完结"}) and "在途" not in x),
                    "package_id"
                ].tolist()
                if history_ids:
                    hist_rows = []
                    for pkg_id in history_ids:
                        g = pack_df[pack_df["package_id"] == pkg_id]
                        books_text, buyers_text, count = package_summary(g)
                        hist_rows.append({"包裹编号": pkg_id, "本数": count, "📚 书籍": books_text, "👤 买家": buyers_text})
                    st.dataframe(pd.DataFrame(hist_rows), hide_index=True, use_container_width=True)
                else:
                    st.info("暂无历史包裹。")
    else:
        st.error("⚠️ 数据库中暂未检测到 `package_id` 字段。")


# ====== TAB 6: 📊 财务与月度营收统计 (双币种智能结算版) ======
if tab6:
    st.markdown("### 📊 财务与月度营收统计")
    st.info("💡 系统已为你开启【跨境双币核算】模式：买家收入为 RMB(¥)，采购与邮费支出为 HKD($)。")
    
    if not df.empty:
        # ================== ⚙️ 结算设置区 ==================
        st.markdown("#### ⚙️ 结算与过滤设置")
        
        # 调整为三列，加入现货/预售筛选器
        c_rate, c_scope, c_type = st.columns([1, 1.5, 1])
        with c_rate:
            current_rate = st.number_input("港币 (HKD) 兑人民币 (RMB) 汇率", value=0.9200, format="%.4f", help="代表 1 港币 = 0.92 人民币")
        with c_scope:
            scope = st.radio(
                "📊 数据统计范围", 
                ["🏆 已确认盈利 (仅统计已发货/完结)", "🔮 预估总盈利 (统计系统内所有订单)"], 
                index=0
            )
        with c_type:
            # 🚀 核心新增：现货与预售分类查看
            stock_filter = st.radio(
                "📦 商品属性筛选",
                ["全部", "现货", "预售"],
                index=0
            )
            
        st.write("---")
        
        # 🚀 核心逻辑 1：根据发货状态过滤
        if "已确认盈利" in scope:
            calc_df = df[df["status"].isin(["卖家已发货", "已完结"])].copy()
        else:
            calc_df = df.copy()
            
        # 🚀 核心逻辑 2：根据现货/预售过滤
        if stock_filter != "全部":
            calc_df = calc_df[calc_df["stock_type"] == stock_filter].copy()
        
        if calc_df.empty:
            st.warning(f"⚠️ 当前范围内没有任何符合【{scope.split(' ')[0]} + {stock_filter}】条件的订单，暂无数据可计算。")
        else:
            # ================== 💰 核心财务数据计算 ==================
            # 1. 总收入 (纯 RMB)
            total_income_rmb = calc_df["price_sell"].sum()
            
            # 2. 总支出 (纯 HKD = 书本采购 + 海外运费)
            total_book_cost_hkd = calc_df["price_buy"].sum()
            total_shipping_cost_hkd = calc_df["shipping_fee"].sum()
            total_expense_hkd = total_book_cost_hkd + total_shipping_cost_hkd
            
            # 3. 折算与利润 (转回 RMB)
            converted_expense_rmb = total_expense_hkd * current_rate
            net_profit_rmb = total_income_rmb - converted_expense_rmb
            
            # ================== 📈 数据大屏展示 ==================
            st.markdown(f"#### 📈 总体营收看板 - {scope.split(' ')[0]} ({stock_filter})")
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric(label="💰 总收入 (RMB)", value=f"¥ {total_income_rmb:,.2f}")
            with col2:
                st.metric(label="🛒 采购及运费总支出 (HKD)", value=f"HK$ {total_expense_hkd:,.2f}", 
                          delta=f"折合 RMB: -¥{converted_expense_rmb:,.2f}", delta_color="inverse")
            with col3:
                st.metric(label="🏆 净利润 (RMB)", value=f"¥ {net_profit_rmb:,.2f}")
                
            st.write("---")
            
           # ================== 📅 月度盈利统计明细 ==================
            st.markdown(f"#### 📅 月度盈利统计明细 ({stock_filter})")
            st.caption(f"这里展示每个月的总体收支情况（当前统计范围：**{scope.split(' ')[0]}**），HKD 支出已自动按上方汇率折算为 RMB。")
            
            if "order_time" in calc_df.columns:
                valid_time_df = calc_df[calc_df["order_time"].notna() & (calc_df["order_time"] != "")].copy()
                
                if not valid_time_df.empty:
                    valid_time_df["年月"] = pd.to_datetime(valid_time_df["order_time"]).dt.strftime('%Y-%m')
                    
                    # 按月份进行分组核算
                    monthly_summary = valid_time_df.groupby("年月").agg(
                        售出书本数=("id", "count"),
                        月度总收入_RMB=("price_sell", "sum"),
                        月度采购支出_HKD=("price_buy", "sum"),
                        月度邮费支出_HKD=("shipping_fee", "sum")
                    ).reset_index()
                    
                    # 计算每个月的总支出(HKD) 和 最终利润(RMB)
                    monthly_summary["总支出 (HK$)"] = monthly_summary["月度采购支出_HKD"] + monthly_summary["月度邮费支出_HKD"]
                    monthly_summary["折合支出_RMB"] = monthly_summary["总支出 (HK$)"] * current_rate
                    monthly_summary["净利润 (¥)"] = monthly_summary["月度总收入_RMB"] - monthly_summary["折合支出_RMB"]
                    
                    # 重命名列让表格更直观
                    monthly_display = monthly_summary.rename(columns={
                        "年月": "月份",
                        "售出书本数": "成单量",
                        "月度总收入_RMB": "总收入 (¥)"
                    })
                    
                    # 按照月份倒序排列（最新的月份排在最上面）
                    monthly_display = monthly_display.sort_values(by="月份", ascending=False)
                    
                    # 丢进前端展示
                    st.dataframe(
                        monthly_display[["月份", "成单量", "总收入 (¥)", "总支出 (HK$)", "净利润 (¥)"]].style.format({
                            "总收入 (¥)": "{:.2f}",
                            "总支出 (HK$)": "{:.2f}",
                            "净利润 (¥)": "{:.2f}"
                        }), 
                        hide_index=True, 
                        use_container_width=True
                    )
                else:
                    st.info("尚无有效的订单时间以供月度分析。")
            else:
                st.info("尚无带有时间记录的订单以供月度分析。")
                
    else:
        st.info("系统暂无任何订单数据。")
