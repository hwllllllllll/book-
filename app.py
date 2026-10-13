import streamlit as st    
import pandas as pd
import datetime
import uuid
import time
import io
import hashlib
import csv
import re
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
STATUSES = ["买家已下单", "我方已下单", "已合包", "在途", "已到货", "官方已发货", "卖家已发货", "已完结"]

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
    
    /* 调整分割线更柔和 */
    hr {
        margin: 1.5rem 0;
        border-color: #e2e8f0;
    }
</style>
""", unsafe_allow_html=True)

st.title("☁️ 图书后台 ")

# ==========================================
# 🚀 数据加载优化
# ==========================================
# 只加载订单业务字段，不读取图片字段，减少数据库返回量和网络流量。
ORDER_COLUMNS = (
    "id,buyer_name,book_name,shop_name,status,"
    "price_sell,price_buy,shipping_fee,package_id,order_time,"
    "stock_type,deadline,official_cutoff_time,official_shipping_time,"
    "buyer_address,pickup_area"
)

@st.cache_data(ttl=120, show_spinner=False)
def load_data():
    try:
        rows = []
        page_size = 1000
        offset = 0

        while True:
            response = (
                supabase.table("orders")
                .select(ORDER_COLUMNS)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        result = pd.DataFrame(rows)

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



df = load_data()

# 1. 初始化导航状态
if "nav_selection" not in st.session_state:
    st.session_state["nav_selection"] = "🏠 工作台"

# 2. 检查是否有跳转请求（在菜单渲染前安全处理）
if st.session_state.get("pending_redirect_t1", False):
    st.session_state["nav_selection"] = "📝 常规录入"
    st.session_state["pending_redirect_t1"] = False 

# 手机友好的功能下拉菜单导航
menu_options = [
    "🏠 工作台",
    "📝 常规录入",
    "📥 批量录入",
    "🔎 订单管理",
    "📚 书籍资料库",
    "📋 现货", 
    "🔮 预售", 
    "📦 包裹合拼与运费",  
    "🚚 发货看板",        
    "📊 月度营收统计"
]

selected_tab = st.selectbox("📌 请选择功能页面", menu_options, key="nav_selection", label_visibility="collapsed")

# 确保这里的判断文本和上面的列表一字不差
tab0 = (selected_tab == "🏠 工作台")
tab_batch = (selected_tab == "📥 批量录入")
tab_orders = (selected_tab == "🔎 订单管理")
tab_catalog = (selected_tab == "📚 书籍资料库")
tab1 = (selected_tab == "📝 常规录入")
tab2 = (selected_tab == "📋 现货") 
tab3 = (selected_tab == "🔮 预售")
tab5 = (selected_tab == "📦 包裹合拼与运费") 
tab4 = (selected_tab == "🚚 发货看板")       
tab6 = (selected_tab == "📊 月度营收统计")


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

def load_catalog():
    """书籍表为可选项；未创建时仍可从历史订单复用书名。"""
    try:
        response = supabase.table("book_catalog").select("id,book_name,edition,stock_type,official_cutoff_time,official_shipping_time,default_price").order("book_name").limit(1000).execute()
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
    st.subheader("🏠 今日工作台")
    if df.empty:
        st.info("暂无订单。可从『常规录入』或『批量录入』开始。")
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
        d.metric("📦 可安排发货", count_where(["已到货", "已合包", "官方已发货"]))
        deadline_df = df[df["status"].ne("卖家已发货") & df["status"].ne("已完结")].copy()
        deadline_df["剩余天数"] = (pd.to_datetime(deadline_df["deadline"], errors="coerce").dt.normalize() - pd.Timestamp.today().normalize()).dt.days
        urgent = deadline_df[deadline_df["剩余天数"].notna() & (deadline_df["剩余天数"] <= 5)].sort_values("剩余天数")
        st.markdown("#### ⏰ 急需处理的订单（5 天内截止或逾期）")
        if urgent.empty:
            st.success("目前没有接近发货截止日期的未完结订单。")
        else:
            st.dataframe(urgent[["id", "buyer_name", "book_name", "status", "剩余天数"]].rename(columns={"id":"编号","buyer_name":"买家","book_name":"书籍","status":"状态"}).head(80), use_container_width=True, hide_index=True)
        st.markdown("#### ⚡ 常用操作")
        ca,cb,cc = st.columns(3)
        def go_to(dest):
            st.session_state["nav_selection"] = dest
        ca.button("➕ 录入订单", use_container_width=True, on_click=go_to, args=("📝 常规录入",))
        cb.button("📥 批量导入", use_container_width=True, on_click=go_to, args=("📥 批量录入",))
        cc.button("🔎 搜索订单", use_container_width=True, on_click=go_to, args=("🔎 订单管理",))

if tab_batch:
    st.subheader("📥 多买家批量录入")
    st.caption("每一行代表一种书；同一批次同一买家可录入多款书。支持复制 Excel 的 TSV 或 CSV。为了防止误识别，导入前必须预览确认。")
    sample = """买家账号\t书名\t版本\t数量\t订单总收款\t店铺\t商品属性\t下单日期\t批次号\t每本售价
小明\t示例书A\t普装\t2\t120\t大号\t现货\t2026-10-13\tA001\t
小明\t示例书B\t特装\t1\t120\t大号\t现货\t2026-10-13\tA001\t
小红\t示例书C\t普装\t1\t68\t小号\t预售\t2026-10-13\tA002\t"""
    st.download_button("📄 下载 Excel 可用的示范 TSV", data=sample.encode("utf-8-sig"), file_name="orders_batch_template.tsv", mime="text/tab-separated-values")
    upload = st.file_uploader("上载 CSV / TSV 文件", type=["csv", "tsv", "txt"], key="batch_file")
    pasted = st.text_area("或直接贴上 Excel 表格", height=155, key="batch_text")
    with st.expander("欄位使用說明", expanded=False):
        st.write("必填：买家账号、书名、数量。可选：版本（默认普装）、订单总收款、店铺、商品属性、下单日期、批次号、每本售价。")
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
                    order_day = str(rec.get("下单日期", "") or datetime.date.today().isoformat()).strip()
                    order_day = datetime.date.fromisoformat(order_day).isoformat()
                    batch_no = str(rec.get("批次号", "") or f"行{line}").strip()
                    total = str(rec.get("订单总收款", "") or "").strip()
                    unit = str(rec.get("每本售价", "") or "").strip()
                    if total and (not Decimal(total).is_finite() or Decimal(total) < 0): raise ValueError("总收款不能为负数")
                    if unit and (not Decimal(unit).is_finite() or Decimal(unit) < 0): raise ValueError("单价不能为负数")
                    if unit and Decimal(unit).as_tuple().exponent < -2: raise ValueError("单价最多两位小数")
                    if total and Decimal(total).as_tuple().exponent < -2: raise ValueError("总额最多两位小数")
                    items.append({"buyer":buyer,"book":book,"edition":edition,"qty":qty,"shop":shop,"kind":kind,"day":order_day,"batch":batch_no,"total":total,"unit":unit})
                except Exception as exc: errors.append(f"第 {line} 行：{exc}")
            groups={}
            for item in items:
                key=(item["buyer"],item["batch"])
                groups.setdefault(key,[]).append(item)
            output=[]
            for key, rows in groups.items():
                if len({(r["shop"],r["kind"],r["day"]) for r in rows}) != 1:
                    errors.append(f"买家 {key[0]} 批次 {key[1]}：店铺/属性/日期不一致"); continue
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
                        output.append({"buyer_name":r["buyer"], "book_name":f'{r["book"]}（{r["edition"]}）', "shop_name":r["shop"], "stock_type":r["kind"],"status":"买家已下单", "order_time":f'{r["day"]}T12:00:00', "deadline":(datetime.date.fromisoformat(r["day"])+datetime.timedelta(days=15)).isoformat(),"price_sell":prices[pos],"price_buy":0.0,"purchase_type":"合并拼单","official_cutoff_time":"","official_shipping_time":""})
                        pos+=1
            if errors:
                st.error("请修正以下问题后重新导入：\n" + "\n".join(errors[:20]))
            elif not output:
                st.warning("没有可录入的订单。")
            else:
                preview=pd.DataFrame(output)
                st.success(f"校验成功：{len(groups)} 个订单批次、{len(output)} 本书、收款 ¥{preview['price_sell'].sum():.2f}")
                st.dataframe(preview[["buyer_name","book_name","shop_name","stock_type","price_sell"]].head(100).rename(columns={"buyer_name":"买家","book_name":"书籍","shop_name":"店铺","stock_type":"类型","price_sell":"单本售价"}),hide_index=True,use_container_width=True)
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
                            supabase.table("orders").insert(output[start:start+100]).execute()
                            saved+=len(output[start:start+100])
                        st.session_state["batch_last_success"] = fingerprint
                        clear_cached_data(); st.success(f"✅ 成功新增 {saved} 本。请勿重复提交同一份资料。")
                    except Exception as exc:
                        clear_cached_data(); st.error(f"保存中断：已确认成功写入前 {saved} 本；其余不确定，请先到订单管理核对，勿直接整批重试。错误：{exc}")
        except Exception as exc:
            st.error(f"无法解析资料：{exc}")

if tab_orders:
    st.subheader("🔎 搜尋與編輯訂單")
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
            filtered=filtered[filtered[["buyer_name","book_name","package_id"]].fillna("").astype(str).apply(lambda col:col.str.contains(needle,case=False,regex=True)).any(axis=1)]
        if sf!="全部": filtered=filtered[filtered["status"].eq(sf)]
        if kf!="全部": filtered=filtered[filtered["stock_type"].eq(kf)]
        filtered=filtered.sort_values("order_time", ascending=False)
        count=len(filtered); pages=max(1,(count+49)//50)
        st.caption(f"找到 {count} 筆，每頁顯示 50 筆，共 {pages} 頁")
        shown=filtered.iloc[(int(page)-1)*50:int(page)*50]
        if not shown.empty:
            st.dataframe(shown[["id","buyer_name","book_name","status","price_sell","price_buy","package_id","order_time"]].rename(columns={"id":"編號","buyer_name":"買家","book_name":"書籍","status":"狀態","price_sell":"售價 RMB","price_buy":"採購價 HKD","package_id":"包裹","order_time":"下單時間"}), use_container_width=True,hide_index=True)
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
                update=st.form_submit_button("💾 保存修改",type="primary")
            if update:
                if not ebuyer.strip() or not ebook.strip(): st.error("買家及書名不可留空")
                else:
                    try:
                        supabase.table("orders").update({"buyer_name":ebuyer.strip(),"book_name":ebook.strip(),"status":estat,"shop_name":eshop,"stock_type":ekind,"price_sell":eprice,"price_buy":ecost}).eq("id",int(chosen)).execute()
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
    st.subheader("📚 書籍資料庫")
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
            cutoff=st.date_input("官方截單日期",value=datetime.date.today())
            shipping=st.date_input("預計發貨日期",value=datetime.date.today()+datetime.timedelta(days=30))
            add=st.form_submit_button("➕ 儲存書籍",type="primary")
        if add:
            if not n.strip(): st.error("請輸入書名")
            else:
                try:
                    supabase.table("book_catalog").upsert({"book_name":n.strip(),"edition":v,"stock_type":kind,"default_price":default_price,"official_cutoff_time":cutoff.isoformat(),"official_shipping_time":shipping.isoformat()},on_conflict="book_name,edition").execute()
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
    book_default_cutoff = {}
    book_default_shipping = {}
    if not df.empty and "book_name" in df.columns:
        # 去重书名；日期只供复用，不影响现有订单。
        for row in df.itertuples(index=False):
            b_raw = str(getattr(row, "book_name", ""))
            if not b_raw or b_raw == "nan":
                continue
            base = b_raw.split("（")[0].split("(")[0].strip()
            if not base:
                continue
            existing_books.append(base)
            cutoff = getattr(row, "official_cutoff_time", None)
            shipping = getattr(row, "official_shipping_time", None)
            if pd.notna(cutoff) and cutoff:
                book_default_cutoff[base] = cutoff
            if pd.notna(shipping) and shipping:
                book_default_shipping[base] = shipping
    # 读取可选的持久书籍库；未建表不影响原有录入。
    catalog_df, catalog_ok = load_catalog()
    if catalog_ok and not catalog_df.empty:
        for rec in catalog_df.to_dict("records"):
            base = str(rec.get("book_name") or "").strip()
            if not base:
                continue
            existing_books.append(base)
            if rec.get("official_cutoff_time"):
                book_default_cutoff[base] = rec["official_cutoff_time"]
            if rec.get("official_shipping_time"):
                book_default_shipping[base] = rec["official_shipping_time"]
    existing_books = sorted(set(existing_books))

    if not df.empty and "buyer_name" in df.columns:
        previous_buyers = sorted(set(x for x in df["buyer_name"].dropna().astype(str) if x.strip()))
        def select_previous_buyer():
            chosen = st.session_state.get("t1_previous_buyer", "-- 新买家 --")
            if chosen != "-- 新买家 --":
                st.session_state["t1_buyer"] = chosen
        st.selectbox("🔍 快速复用历史买家（可选）", ["-- 新买家 --"] + previous_buyers, key="t1_previous_buyer", on_change=select_previous_buyer)
        candidate = st.session_state.get("t1_buyer", "")
        if candidate:
            previous = df[df["buyer_name"].astype(str).eq(candidate)]
            if not previous.empty:
                pending = previous[~previous["status"].isin(["卖家已发货","已完结"])]
                st.caption(f"👤 {candidate} 已有 {len(previous)} 本历史书籍，其中 {len(pending)} 本尚未向买家发货。")

    entry_mode = st.radio("录入方式", ["⚡ 极速录入", "📚 逐本录入"], horizontal=True, key="t1_mode")
    with st.form("t1_order_form", clear_on_submit=False):
        a, b = st.columns(2)
        with a:
            buyer = st.text_input("买家账号 *", key="t1_buyer")
            p_sell_total = st.number_input("买家付款总额 (RMB ¥)", min_value=0.0, step=1.0, format="%.2f", key="t1_price_editable")
            shop = st.selectbox("下单店铺", SHOPS, key="t1_shop")
            keep_buyer = st.checkbox("保存后继续录入同一买家", key="t1_keep_buyer", help="连续录入同一买家的不同订单时不用重复输入账号")
        with b:
            stock_type = st.radio("商品属性", ["现货", "预售"], horizontal=True, key="t1_stock_type")
            status = st.selectbox("当前订单状态", STATUSES, key="t1_status")
            input_date = st.date_input("买家下单日期", value=datetime.date.today(), key="t1_date")
            input_time = st.time_input("买家下单时间", value=datetime.datetime.now().time().replace(second=0, microsecond=0), key="t1_time")
        auto_deadline = input_date + datetime.timedelta(days=15)
        st.caption(f"预计发货截止：{auto_deadline:%Y-%m-%d}（沿用原有 15 天规则）")

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

        official_cutoff = ""
        official_shipping = ""
        if stock_type == "预售":
            first_book_name = book_entries[0]["name"] if book_entries else ""
            default_cutoff = datetime.date.today()
            default_shipping = datetime.date.today() + datetime.timedelta(days=30)
            try:
                if first_book_name in book_default_cutoff:
                    default_cutoff = pd.to_datetime(book_default_cutoff[first_book_name]).date()
                if first_book_name in book_default_shipping:
                    default_shipping = pd.to_datetime(book_default_shipping[first_book_name]).date()
            except (ValueError, TypeError):
                pass
            pc1, pc2 = st.columns(2)
            with pc1:
                official_cutoff = st.date_input("官方截单日期", value=default_cutoff).isoformat()
            with pc2:
                official_shipping = st.date_input("预计官方发货日期", value=default_shipping).isoformat()

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
                "buyer_name": buyer.strip(),
                "book_name": f"{item['name']}（{item['edition']}）",
                "shop_name": shop,
                "status": status,
                "price_sell": split_prices[i],
                "price_buy": 0.0,
                "purchase_type": "合并拼单",
                "order_time": combined_datetime,
                "stock_type": stock_type,
                "deadline": auto_deadline.isoformat(),
                "official_cutoff_time": official_cutoff,
                "official_shipping_time": official_shipping,
            } for i, item in enumerate(book_entries)]
            try:
                with st.spinner("正在保存订单..."):
                    supabase.table("orders").insert(order_rows).execute()
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
                    spot_total_cost = st.number_input("这批勾选现货的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入供应商账单总价，系统会自动平摊到这些书的成本中")
                    
                    if st.form_submit_button("⚡ 确认现货已下单并平摊成本", type="primary"):
                        target_ids = selected_rows["订单编号"].tolist()
                        split_cost = spot_total_cost / len(target_ids) if len(target_ids) > 0 else 0.0
                        
                        # 新包裹号不再使用“最早下单时间”，改为“创建时间 + 短码”，避免同一分钟多个包裹撞号。
                        package_batch = make_package_id("现货", selected_rows)
                        
                        batch_update_orders(
                            target_ids,
                            {
                                "status": "我方已下单",
                                "price_buy": split_cost,
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
                "📚 本包书籍": "；".join(f"{b} ×{n}" for b, n in g.groupby("book_name").size().items()),
                "👤 买家": "、".join(str(x) for x in g["buyer_name"].dropna().unique() if str(x).strip()),
                "本包本数": len(g),
                "原始订单ids": list(g["id"]),
            })
        spot_ship_summary = pd.DataFrame(grouped)
        spot_ship_summary.insert(0, "☑️ 已发货", False)

        edited_spot_ship = st.data_editor(
            spot_ship_summary[["☑️ 已发货", "package_id", "本包本数", "📚 本包书籍", "👤 买家"]],
            column_config={
                "☑️ 已发货": st.column_config.CheckboxColumn("☑️ 勾选已发货", default=False),
                "本包本数": st.column_config.NumberColumn("本数", format="%d 本"),
            },
            disabled=["package_id", "本包本数", "📚 本包书籍", "👤 买家"],
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
            
            presale_summary = presale_wait_df.groupby(group_cols).agg(
                待下单数量=("id", "count"),
                official_cutoff_time=("official_cutoff_time", "min"), 
                official_shipping_time=("official_shipping_time", "min"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            presale_summary = presale_summary.sort_values(by="待下单数量", ascending=False)
            
            presale_summary = presale_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 最早截单时间",
                "official_shipping_time": "🚚 最早发货时间",
                "待下单数量": "🔥 待下单总数"
            })
            
            presale_summary.insert(0, "选择下单", False)
            presale_summary.insert(1, "本次下单数量", presale_summary["🔥 待下单总数"])
            
            # 🎯 核心优化：动态生成下拉菜单的最大选项数量
            max_possible_qty = int(presale_summary["🔥 待下单总数"].max())
            # 生成 [1, 2, 3, ... max] 的列表作为下拉选项
            qty_options = list(range(1, max_possible_qty + 1))
            
            cols_order = ["选择下单", "本次下单数量", "🔥 待下单总数", "📖 预售书名", "⏰ 最早截单时间", "🚚 最早发货时间", "买家列表"]
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
                disabled=["🔥 待下单总数", "📖 预售书名", "⏰ 最早截单时间", "🚚 最早发货时间", "买家列表"],
                use_container_width=True,
                key="presale_summary_editor"
            )
            
            selected_pre_rows = edited_presale[edited_presale["选择下单"] == True]
            
            if not selected_pre_rows.empty:
                st.markdown(f"#### 🎯 已勾选了 **{len(selected_pre_rows)}** 款预售书准备下单")
                
                with st.form("presale_batch_form"):
                    pre_total_cost = st.number_input("这批勾选预售书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入总价，系统会自动平摊")
                    
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary"):
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
        st.info("📦 这里显示的是你【已经向官方下单】但还没发货的预售款。时刻盯紧发货日期！")
        
        # 🎯 筛选预售且状态为“我方已下单”的需求
        presale_shipping_df = df[(df["stock_type"] == "预售") & (df["status"] == "我方已下单")].copy()
        
        if not presale_shipping_df.empty:
            group_cols = ["book_name", "official_cutoff_time", "official_shipping_time"]
            shipping_summary = presale_shipping_df.groupby(group_cols).agg(
                等待发货数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            # 按发货时间排序，越早发货的排在越前面
            shipping_summary = shipping_summary.sort_values(by="official_shipping_time", ascending=True)
            
            shipping_summary = shipping_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 官方截单时间",
                "official_shipping_time": "🚚 预计官方发货时间",
                "等待发货数量": "⏳ 苦等发货本数"
            })
            
            shipping_summary.insert(0, "标记已发货", False)
            cols_order_ship = ["标记已发货", "⏳ 苦等发货本数", "📖 预售书名", "🚚 预计官方发货时间", "⏰ 官方截单时间", "买家列表"]
            available_ship_cols = [c for c in cols_order_ship if c in shipping_summary.columns]
            
            edited_shipping = st.data_editor(
                shipping_summary[available_ship_cols],
                column_config={
                    "标记已发货": st.column_config.CheckboxColumn("勾选已发货", default=False),
                    "⏳ 苦等发货本数": st.column_config.NumberColumn("⏳ 待发本数", format="%d 本")
                },
                disabled=["⏳ 苦等发货本数", "📖 预售书名", "⏰ 官方截单时间", "🚚 预计官方发货时间", "买家列表"],
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

# ====== TAB 4: 自动发货与取件码汇总 ======
if tab4:
    sub_col1, sub_col2 = st.columns([3, 1])
    with sub_col1:
        st.subheader("🚚 待发货包裹智能看板 (手机适配版)")
    with sub_col2:
        if st.button("🔄 刷新看板", key="refresh_shipping"):
            st.rerun()
            
    st.info("💡 手机端优化版：自动屏蔽未到货商品！仅显示【官方已发货/已到达我方仓库/已合包/已到货】的书籍。如果买家还有书没到，系统会智能提醒！")
    
    if not df.empty:
        # 🎯 核心升级 1：严格过滤，只有这四种状态的书才有资格进入发货看板！
        allowed_statuses = ["官方已发货", "已到达我方仓库", "已合包", "已到货"]
        shipping_df = df[
            (df["status"].isin(allowed_statuses)) &
            (df["buyer_name"] != "暂无")
        ].copy()

        if not shipping_df.empty:

            today_date = datetime.date.today()
            def calc_remaining_days(d_str):
                try:
                    if not d_str: return 999
                    return (pd.to_datetime(d_str).date() - today_date).days
                except:
                    return 999

            shipping_df["remaining_days"] = shipping_df["deadline"].apply(calc_remaining_days)
            group_cols = [c for c in ["buyer_name", "shop_name"] if c in shipping_df.columns]
            
            grouped = list(shipping_df.groupby(group_cols))
            
            def get_min_days(g_item):
                return g_item[1]["remaining_days"].min()
            
            grouped = sorted(grouped, key=get_min_days)
            
            for name_key, group in grouped:
                b_name = name_key[0]
                s_name = name_key[1] if len(name_key) > 1 else ""
                
                books = group["book_name"].tolist()
                statuses = group["status"].tolist()
                
                # 🎯 核心升级 2：全局去数据库查一下，这个买家是不是还有别的东西没到？
                buyer_all_orders = df[df["buyer_name"] == b_name]
                unarrived_orders = buyer_all_orders[~buyer_all_orders["status"].isin(allowed_statuses + ["卖家已发货", "已完结"])]
                has_unarrived = not unarrived_orders.empty
                
                min_days = group["remaining_days"].min()
                total_sell = group["price_sell"].sum()
                
                current_address = group["buyer_address"].iloc[0] if group["buyer_address"].iloc[0] else ""
                current_pickup = group["pickup_area"].iloc[0] if group["pickup_area"].iloc[0] else ""
                
                if min_days == 999: days_str = "无限制"
                elif min_days < 0: days_str = f"🔴 已超期 {-min_days} 天"
                elif min_days == 0: days_str = "⚠️ 今天截止"
                elif min_days <= 5: days_str = f"🔥 仅剩 {min_days} 天"
                else: days_str = f"⏳ 剩 {min_days} 天"
                
                card_title = f"📦 买家: {b_name} | 店铺: {s_name} | 总额: ¥{total_sell:.2f} | 倒计时: {days_str}"
                
                # 如果他还有没到货的书，标题直接变红警告
                if has_unarrived:
                    card_title = f"🔴【还有未到货】{card_title}"
                
                with st.expander(card_title, expanded=False):
                    if has_unarrived:
                        # 🎯 提取未到货的书名给卖家提个醒，防呆设计！
                        un_list = [f"{row['book_name']} [{row['status']}]" for _, row in unarrived_orders.iterrows()]
                        st.warning(f"⚠️ 强烈建议等齐再发！该买家还有以下商品未到货（已自动从下方发货列表隐藏）：\n\n{' / '.join(un_list)}")
                    
                    st.markdown("##### 📖 本次可发货书单明细：")
                    for idx, (b_item, st_val) in enumerate(zip(books, statuses)):
                        st.markdown(f"- **书本 {idx+1}**：{b_item} `({st_val})`")
                        
                    st.write("---")
                    
                    with st.form(key=f"form_shipping_{b_name}_{s_name}"):
                        f_col1, f_col2 = st.columns(2)
                        with f_col1:
                            new_addr = st.text_area("📍 收货地址", value=current_address, height=80)
                        with f_col2:
                            new_pickup = st.text_input("🏷️ 取件码", value=current_pickup)
                            
                        act_col1, act_col2 = st.columns(2)
                        with act_col1:
                            save_btn = st.form_submit_button("💾 保存此买家地址/取件码", type="secondary")
                        with act_col2:
                            # 统一变更为【卖家已发货】
                            ship_btn = st.form_submit_button("🚀 一键标记该买家【已发货】", type="primary")
                            
                        group_ids = group["id"].tolist()

                        if save_btn:
                            batch_update_orders(
                                group_ids,
                                {
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }
                            )
                            clear_cached_data()
                            st.success(f"✅ 买家【{b_name}】的收货地址与取件码已更新！")
                            st.rerun()

                        if ship_btn:
                            batch_update_orders(
                                group_ids,
                                {
                                    "status": "卖家已发货",
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }
                            )
                            clear_cached_data()
                            st.success(f"🚀 买家【{b_name}】的可发货订单已成功发出！")
                            st.rerun()

        else:
            st.info("📦 当前没有任何买家的包裹处于【官方已发货/已到达我方仓库/已合包】状态。")
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
