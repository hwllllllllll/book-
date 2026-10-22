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
ALLOCATION_SWAP_SQL = "create table if not exists public.order_allocation_swaps (\n  request_id uuid primary key,\n  created_at timestamptz not null default now(),\n  urgent_order_id bigint not null,\n  delayed_order_id bigint not null,\n  reason text not null,\n  urgent_before jsonb not null,\n  delayed_before jsonb not null\n);\nalter table public.order_allocation_swaps enable row level security;\nrevoke all on public.order_allocation_swaps from anon, authenticated;\ngrant select, insert on public.order_allocation_swaps to service_role;\n\ncreate or replace function public.swap_order_allocation(\n  p_request_id uuid, p_urgent_id bigint, p_delayed_id bigint,\n  p_urgent_expected jsonb, p_delayed_expected jsonb,\n  p_urgent_notice timestamptz, p_delayed_notice timestamptz,\n  p_reason text, p_notice_ack boolean\n) returns uuid\nlanguage plpgsql security invoker set search_path = public\nas $$\ndeclare\n  a public.orders%rowtype;\n  b public.orders%rowtype;\nbegin\n  perform pg_advisory_xact_lock(hashtextextended(p_request_id::text, 0));\n  if exists (select 1 from public.order_allocation_swaps where request_id=p_request_id) then\n    return p_request_id;\n  end if;\n  if p_urgent_id=p_delayed_id or length(trim(coalesce(p_reason,'')))=0 then\n    raise exception '請選擇兩筆不同訂單並填寫原因';\n  end if;\n  perform id from public.orders where id in (p_urgent_id,p_delayed_id) order by id for update;\n  select * into a from public.orders where id=p_urgent_id;\n  if not found then raise exception '加急訂單不存在'; end if;\n  select * into b from public.orders where id=p_delayed_id;\n  if not found then raise exception '延後訂單不存在'; end if;\n  if p_urgent_expected is null or p_delayed_expected is null\n     or not (to_jsonb(a) @> p_urgent_expected) or not (to_jsonb(b) @> p_delayed_expected)\n     or a.shipping_notified_at is distinct from p_urgent_notice\n     or b.shipping_notified_at is distinct from p_delayed_notice then\n    raise exception '訂單已變動，請重新整理後核對';\n  end if;\n  if a.status is null or b.status is null or a.status not in ('我方已下单','官方已发货','已合包','在途','已到达我方仓库')\n     or b.status <> '已到货' then\n    raise exception '只可交換已採購未到貨與已到貨未寄出的訂單';\n  end if;\n  if a.book_name is null or b.book_name is null or a.book_name is distinct from b.book_name or a.stock_type is distinct from b.stock_type then\n    raise exception '書名、版本及商品類型必須完全相同';\n  end if;\n  if a.buyer_name is null or b.buyer_name is null or a.buyer_name is not distinct from b.buyer_name\n     or a.buyer_name in ('暂无','暫無') or b.buyer_name in ('暂无','暫無') then\n    raise exception '必須選擇兩位不同買家的訂單';\n  end if;\n  if coalesce(a.package_id,'')='' or coalesce(b.package_id,'')='' or a.package_id=b.package_id then\n    raise exception '兩筆訂單必須有不同的有效包裹編號';\n  end if;\n  if b.shipping_notified_at is not null and not coalesce(p_notice_ack,false) then\n    raise exception '延後買家已獲通知，請先核對並確認';\n  end if;\n  insert into public.order_allocation_swaps(request_id,urgent_order_id,delayed_order_id,reason,urgent_before,delayed_before)\n    values(p_request_id,a.id,b.id,trim(p_reason),to_jsonb(a),to_jsonb(b));\n  update public.orders set status=b.status, package_id=b.package_id,\n    price_buy=b.price_buy, shipping_fee=b.shipping_fee, purchase_vendor=b.purchase_vendor,\n    shipping_notified_at=null where id=a.id;\n  update public.orders set status=a.status, package_id=a.package_id,\n    price_buy=a.price_buy, shipping_fee=a.shipping_fee, purchase_vendor=a.purchase_vendor,\n    shipping_notified_at=null where id=b.id;\n  return p_request_id;\nend;\n$$;\nrevoke all on function public.swap_order_allocation(uuid,bigint,bigint,jsonb,jsonb,timestamptz,timestamptz,text,boolean) from public, anon, authenticated;\ngrant execute on function public.swap_order_allocation(uuid,bigint,bigint,jsonb,jsonb,timestamptz,timestamptz,text,boolean) to service_role;\nnotify pgrst, 'reload schema';\n"

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
    return supabase.table("orders").update(values).in_("id", ids).execute()


SWAP_WAITING_STATUSES = {"我方已下单", "官方已发货", "已合包", "在途", "已到达我方仓库"}


def swap_expected(row):
    fields = ["book_name", "stock_type", "status", "package_id", "price_buy", "shipping_fee", "purchase_vendor"]
    # Costs are normalized for display elsewhere; omit them here and retain exact
    # database cost values inside the transaction when moving each physical copy.
    fields = [f for f in fields if f not in {"price_buy", "shipping_fee"}]
    out = {f: (None if pd.isna(row.get(f)) else row.get(f)) for f in fields}
    # Blank supplier is the display representation of both null and empty text.
    if not out.get("purchase_vendor"):
        out.pop("purchase_vendor", None)
    out["buyer_name"] = row.get("buyer_name_original", row["buyer_name"])
    return out


def eligible_swap_donors(orders, urgent):
    return orders[
        orders["status"].eq("已到货")
        & orders["book_name"].eq(urgent["book_name"])
        & orders["stock_type"].eq(urgent["stock_type"])
        & ~orders["buyer_name"].isin([urgent["buyer_name"], "暂无", "暫無", ""])
        & orders["package_id"].fillna("").ne("")
        & orders["package_id"].ne(urgent["package_id"])
    ].sort_values(["order_time", "id"], kind="stable")


def render_allocation_swap(orders):
    with st.expander("🔄 同款調貨／加急發貨", expanded=False):
        st.caption("每次交換一本。加急買家取得已到貨的實物；延後買家接手原本未到貨的那一本。採購成本、運費及平台跟實物包裹走，售價與下單時間保留。")
        with st.expander("首次啟用同款調貨：SQL 設定"):
            st.write("在 Supabase SQL Editor 執行以下 SQL；須先完成通知發貨及下單平台的欄位設定。")
            st.code(ALLOCATION_SWAP_SQL, language="sql")
            st.write("此功能使用伺服器端 service_role 金鑰；若原有 SUPABASE_KEY 不是該類型，請在 Streamlit Secrets 設定同專案的 BUYER_ALIAS_SERVICE_KEY。不要將金鑰貼到程式碼或聊天。")
        if not ORDER_FEATURES_READY:
            st.info("請先完成通知發貨及下單平台欄位設定。")
            return
        try:
            logs = buyer_alias_client().table("order_allocation_swaps").select("request_id,created_at,urgent_order_id,delayed_order_id,reason,urgent_before,delayed_before").order("created_at", desc=True).limit(100).execute().data or []
        except Exception:
            st.info("調貨功能尚未就緒，請完成上方 SQL 及金鑰設定後重新整理。")
            return
        if orders.empty:
            st.info("目前沒有訂單。")
            return
        waiting = orders[orders["status"].isin(SWAP_WAITING_STATUSES) & orders["package_id"].fillna("").ne("") & ~orders["buyer_name"].isin(["暂无", "暫無", ""])].sort_values(["order_time", "id"])
        if not waiting.empty:
            urgent_id = st.selectbox("需要加急的未到貨訂單", waiting["id"].tolist(), format_func=lambda i: f"#{i} · {waiting.loc[waiting.id.eq(i), 'buyer_name'].iloc[0]} · {waiting.loc[waiting.id.eq(i), 'book_name'].iloc[0]}", key="swap_urgent")
            urgent = waiting.loc[waiting.id.eq(urgent_id)].iloc[0]
            donors = eligible_swap_donors(orders, urgent)
            if donors.empty:
                st.info("沒有同書名、同版本、同商品類型的其他買家已到貨訂單可交換。")
            else:
                donor_id = st.selectbox("選擇可延後的買家及已到貨訂單", donors.id.tolist(), format_func=lambda i: f"#{i} · {donors.loc[donors.id.eq(i), 'buyer_name'].iloc[0]} · {'已通知發貨' if pd.notna(donors.loc[donors.id.eq(i), 'shipping_notified_at'].iloc[0]) else '未通知'}", key=f"swap_donor_{urgent_id}")
                donor = donors.loc[donors.id.eq(donor_id)].iloc[0]
                st.dataframe(pd.DataFrame([
                    {"買家":urgent.buyer_name, "訂單":int(urgent.id), "調貨前":urgent.status, "調貨後":"已到货", "原包裹":urgent.package_id, "新包裹":donor.package_id},
                    {"買家":donor.buyer_name, "訂單":int(donor.id), "調貨前":"已到货", "調貨後":urgent.status, "原包裹":donor.package_id, "新包裹":urgent.package_id},
                ]), hide_index=True, use_container_width=True)
                st.caption("兩筆通知標記都會清除。加急買家需要重新通知；延後買家會繼續等待接手的包裹，該包裹簽收後才可發貨。")
                before_a, before_b = swap_expected(urgent), swap_expected(donor)
                notice_a = urgent.shipping_notified_at.isoformat() if pd.notna(urgent.shipping_notified_at) else None
                notice_b = donor.shipping_notified_at.isoformat() if pd.notna(donor.shipping_notified_at) else None
                fingerprint = hashlib.sha256(json.dumps([int(urgent_id),int(donor_id),before_a,before_b,notice_a,notice_b], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
                notice_ack = False
                if notice_b:
                    st.warning("這位延後買家已收到發貨通知。請先處理延期安排，再確認調貨。")
                    notice_ack = st.checkbox("已核對並處理這位買家的延期通知", key=f"swap_notice_{fingerprint}")
                reason = st.text_input("調貨原因", placeholder="例如：買家需要趕在星期五前收到", key=f"swap_reason_{fingerprint}")
                confirmed = st.checkbox("已核對版本、特典及兩位買家，確認交換以上兩本書的分配", key=f"swap_confirm_{fingerprint}")
                if st.button("確認同款調貨", type="primary", disabled=not (confirmed and reason.strip() and (not notice_b or notice_ack)), key=f"swap_submit_{fingerprint}"):
                    request_key = f"swap_request_{fingerprint}_{hashlib.sha256(reason.strip().encode()).hexdigest()[:12]}"
                    request_id = st.session_state.setdefault(request_key, str(uuid.uuid4()))
                    try:
                        buyer_alias_client().rpc("swap_order_allocation", {
                            "p_request_id":request_id, "p_urgent_id":int(urgent_id), "p_delayed_id":int(donor_id),
                            "p_urgent_expected":before_a, "p_delayed_expected":before_b,
                            "p_urgent_notice":notice_a, "p_delayed_notice":notice_b,
                            "p_reason":reason.strip(), "p_notice_ack":notice_ack,
                        }).execute()
                        clear_cached_data()
                        st.session_state["swap_success"] = "調貨已記錄：加急訂單可安排發貨，延後訂單已改等替換包裹。"
                        st.rerun()
                    except Exception as exc:
                        clear_cached_data()
                        st.error(f"未能確認調貨結果：{exc}。請重新整理查看下方記錄後再操作；相同請求重試不會重複調貨。")
        else:
            st.info("沒有已採購且有包裹編號的未到貨訂單。")
        st.markdown("**最近 100 筆調貨記錄**")
        if logs:
            entries = []
            for log in logs:
                a,b = log["urgent_before"],log["delayed_before"]
                delayed_now = orders.loc[orders.id.eq(log["delayed_order_id"])]
                state = delayed_now.status.iloc[0] if not delayed_now.empty else "訂單已不存在"
                entries.append({"時間":pd.Timestamp(log["created_at"]).tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M"), "商品":a["book_name"], "加急買家":a["buyer_name"], "加急訂單":log["urgent_order_id"], "延後買家":b["buyer_name"], "延後訂單":log["delayed_order_id"], "延後訂單目前狀態":state, "接手待到貨包裹":a["package_id"], "原因":log["reason"]})
            st.dataframe(pd.DataFrame(entries), hide_index=True, use_container_width=True)
        else:
            st.caption("尚未調貨。")


def make_package_id(kind, orders_df):
    """Create a short, unique package code that is easy to read on a phone."""
    now = datetime.datetime.now()
    prefix = "现货" if kind == "现货" else "预售"
    # Example: 现货-0912-2341-K7Q2
    code = uuid.uuid4().hex[:4].upper()
    return f"{prefix}-{now.strftime('%m%d-%H%M')}-{code}"


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
                update=st.form_submit_button("💾 保存修改",type="primary")
            if update:
                if not ebuyer.strip() or not ebook.strip(): st.error("買家及書名不可留空")
                else:
                    try:
                        supabase.table("orders").update({"buyer_name":canonical_buyer(ebuyer),"book_name":ebook.strip(),"status":estat,"shop_name":eshop,"stock_type":ekind,"price_sell":eprice,"price_buy":ecost, **({"purchase_vendor": evendor} if ORDER_FEATURES_READY else {})}).eq("id",int(chosen)).execute()
                        clear_cached_data(); st.success("修改成功"); st.rerun()
                    except Exception as exc: st.error(f"修改失敗：{exc}")
            with st.expander("⚠️ 刪除錯誤錄入的訂單"):
                st.warning("刪除後不可在本頁恢復。只有確認資料錄錯才使用。")
                if st.checkbox(f"確認永久刪除 #{chosen}",key=f"del_ok_{chosen}"):
                    if st.button("刪除此筆訂單",key=f"del_{chosen}"):
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
    sub_col1, sub_col2 = st.columns([3, 1])
    with sub_col1:
        st.subheader("🚚 買家發貨登記")
    with sub_col2:
        if st.button("🔄 刷新看板", key="refresh_shipping"):
            st.rerun()
            
    if st.session_state.get("swap_success"):
        st.success(st.session_state.pop("swap_success"))
    render_allocation_swap(df)

    st.info("只顯示你已簽收（狀態為「已到货」）的書；官方已發貨、已合包或仍在途的商品不會列入。如買家還有書未到，會提示。確認發出後，直接點『登記已發貨』，無須地址或取件碼。")
    
    if not df.empty:
        # 只有我方已签收的商品可进入发货面板；一键签收会设置为「已到货」。
        allowed_statuses = ["已到货"]
        shipping_df = df[
            (df["status"].isin(allowed_statuses)) &
            (df["buyer_name"] != "暂无")
        ].copy()

        if not shipping_df.empty:
            today_date = datetime.date.today()
            st.caption("現貨按原有截止日期顯示；預售只顯示已等待天數，不會因 15 天預設日期誤標超期。")
            view_kind = st.radio(
                "按商品類型查看", ["全部", "现货", "预售", "混合", "未标注"],
                horizontal=True, key="ship_type_filter",
                format_func=lambda x: "全部" if x == "全部" else TYPE_NAMES[x],
            )
            edition_view = st.radio(
                "按預售版本查看買家", ["全部", "含普裝", "含特裝", "普裝＋特裝都有"],
                horizontal=True, key="ship_presale_edition_filter",
            )
            st.caption("版本篩選按買家的未完結預售訂單判斷，包含尚未到貨的書。同一買家的已簽收商品仍一起顯示、一起登記發貨。")
            notification_view = st.radio("通知狀態", ["全部", "未通知", "部分已通知", "已通知發貨"], horizontal=True, key="shipping_notification_filter")
            only_unarrived = st.checkbox("只看還有商品未到貨的買家", value=False, key="ship_only_unarrived")
            group_cols = [c for c in ["buyer_name", "shop_name"] if c in shipping_df.columns]
            grouped = list(shipping_df.groupby(group_cols))
            # 保留原先「最早下單者在前」的排序方式。
            grouped = sorted(grouped, key=lambda x: (
                x[1]["order_time"].min() if x[1]["order_time"].notna().any() else pd.Timestamp.max,
                x[1]["id"].min()
            ))
            all_by_buyer = dict(tuple(df.groupby("buyer_name", sort=False)))
            shown_count = 0

            for name_key, group in grouped:
                group = group.sort_values(["order_time", "id"], kind="stable")
                b_name = name_key[0]
                s_name = name_key[1] if len(name_key) > 1 else ""

                # 同一買家的其他店鋪也要列入「還有未到貨」提醒，維持原有安全規則。
                buyer_all_orders = all_by_buyer[b_name]
                unarrived_orders = buyer_all_orders[
                    ~buyer_all_orders["status"].isin(allowed_statuses + list(FINISHED_STATUSES))
                ].sort_values(["order_time", "id"], kind="stable")
                has_unarrived = not unarrived_orders.empty
                waiting_kind = summarize_order_types(unarrived_orders if has_unarrived else group)
                ready_kind = summarize_order_types(group)

                if view_kind != "全部" and waiting_kind != view_kind:
                    continue
                if only_unarrived and not has_unarrived:
                    continue
                live_orders = buyer_all_orders[~buyer_all_orders["status"].isin(FINISHED_STATUSES)]
                buyer_editions = presale_edition_counts(live_orders)
                if edition_view == "含普裝" and not buyer_editions.get("普装", 0):
                    continue
                if edition_view == "含特裝" and not buyer_editions.get("特装", 0):
                    continue
                if edition_view == "普裝＋特裝都有" and not (buyer_editions.get("普装", 0) and buyer_editions.get("特装", 0)):
                    continue
                ready_editions = presale_edition_label(presale_edition_counts(group))
                waiting_editions = presale_edition_label(presale_edition_counts(unarrived_orders))
                notice_state, notice_count = notification_summary(group)
                if notification_view != "全部" and notice_state != notification_view:
                    continue
                shown_count += 1

                # 可發/未到的類型分開標註。絕不把含預售的整單都當作現貨逾期。
                type_display = TYPE_NAMES[waiting_kind]
                ready_display = TYPE_NAMES[ready_kind]
                type_tag = f"【{type_display}未到】" if has_unarrived else f"【{type_display}可發】"
                if has_unarrived:
                    type_tag = "🔴" + type_tag
                waiting_note = order_waiting_description(buyer_all_orders, today_date)
                total_sell = group["price_sell"].sum()
                first_order_time = group["order_time"].iloc[0]
                first_order_label = first_order_time.strftime("%m/%d %H:%M") if pd.notna(first_order_time) else "未記錄"
                ready_tag = f"【可發：{ready_display}】" if has_unarrived else ""
                card_title = (
                    f"【{notice_state}】{type_tag}{ready_tag} 📦 買家: {b_name} | 最早下單: {first_order_label} "
                    f"| 店鋪: {s_name} | 預售可發：{ready_editions} | 預售未到：{waiting_editions} "
                    f"| 本次可發: ¥{total_sell:.2f} | {waiting_note}"
                )

                with st.expander(card_title, expanded=False):
                    if has_unarrived:
                        un_list = [
                            f"【{TYPE_NAMES[normalize_order_type(row['stock_type'])]}】"
                            f"{row['book_name']} [{row['status']}]"
                            for _, row in unarrived_orders.iterrows()
                        ]
                        st.warning(
                            "⚠️ 這位買家仍有商品未到貨，建議先核對能否分開寄出（亦包含其他店鋪的訂單）：\n\n"
                            + " / ".join(un_list)
                        )
                    st.caption(f"商品分類：待到貨 {type_display if has_unarrived else '無'} ｜ 本次可發 {ready_display}")
                    st.caption(f"預售版本：已簽收可發 {ready_editions} ｜ 尚未到貨 {waiting_editions}")
                    st.markdown("##### 📖 本次可發貨書單明細：")
                    for idx, (_, book_row) in enumerate(group.iterrows()):
                        time_label = book_row["order_time"].strftime("%Y-%m-%d %H:%M") if pd.notna(book_row["order_time"]) else "時間未記錄"
                        kind_label = TYPE_NAMES[normalize_order_type(book_row["stock_type"])]
                        if normalize_order_type(book_row["stock_type"]) == "预售":
                            edition = presale_name_and_edition(book_row["book_name"])[1]
                            kind_label += "・" + {"普装": "普裝", "特装": "特裝", "官网特": "官網特"}.get(edition, edition)
                        st.markdown(f"- **書本 {idx+1}**：【{kind_label}】{book_row['book_name']} `({book_row['status']})` · 下單 {time_label} · 採購：{book_row.get('purchase_vendor') or '未記錄'} · {'已通知' if pd.notna(book_row['shipping_notified_at']) else '未通知'}")

                    st.write("---")
                    
                    st.markdown(f"**通知狀態：{notice_state}（{notice_count}/{len(group)} 本）**")
                    st.caption("通知買家後按下標記，只記錄本次已到貨書單；不會代發訊息。之後新增到貨的書會另顯示為未通知。")
                    notice_dates = group["shipping_notified_at"].dropna()
                    if not notice_dates.empty:
                        latest_notice = notice_dates.max().tz_convert("Asia/Hong_Kong").strftime("%Y-%m-%d %H:%M")
                        st.caption(f"最近通知：{latest_notice}（香港時間）")
                    if st.button("📣 標記已通知發貨", key=f"notify_{b_name}_{s_name}", disabled=(not ORDER_FEATURES_READY or notice_count == len(group)), use_container_width=True):
                        try:
                            pending_ids = group.loc[group["shipping_notified_at"].isna(), "id"].tolist()
                            batch_update_orders(pending_ids, {"shipping_notified_at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
                            clear_cached_data()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"通知記錄保存失敗：{exc}")
                    if notice_count and st.button("撤回通知標記", key=f"unnotify_{b_name}_{s_name}"):
                        try:
                            batch_update_orders(group["id"].tolist(), {"shipping_notified_at": None})
                            clear_cached_data()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"撤回失敗：{exc}")

                    # 本頁只記錄發貨，不要求地址或取件碼。
                    if st.button("✅ 登記這位買家已發貨", type="primary", key=f"ship_{b_name}_{s_name}", use_container_width=True):
                        try:
                            batch_update_orders(group["id"].tolist(), {"status": "卖家已发货"})
                            clear_cached_data()
                            st.success(f"已登記 {b_name} 的 {len(group)} 本書發貨")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"登記失敗：{exc}")

            if shown_count == 0:
                st.info("目前沒有符合這個分類的可發貨訂單，試試切換『全部』或取消勾選篩選。")
        else:
            st.info("📦 目前沒有已簽收、可安排發貨的商品。請在「包裹合拼与运费」的「在途」區域簽收；狀態變為「已到货」後，便會出現在這裡。")
    else:
        st.info("暂无数据。")
        

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
                                order_ids = g["id"].tolist()
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
                            batch_update_orders(g["id"].tolist(), {"status": "已到货"})
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
