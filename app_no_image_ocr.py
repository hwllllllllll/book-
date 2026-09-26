import streamlit as st    
import pandas as pd
import datetime
import uuid            # 👈 新增引入：用于给图片生成不重复的随机文件名
import time
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
    "id,buyer_name,xianyu_no,book_name,shop_name,status,"
    "price_sell,price_buy,shipping_fee,package_id,order_time,"
    "stock_type,deadline,official_cutoff_time,official_shipping_time,"
    "buyer_address,pickup_area"
)

@st.cache_data(ttl=300, show_spinner=False)
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
    st.session_state["nav_selection"] = "📝 常规录入"

# 2. 检查是否有跳转请求（在菜单渲染前安全处理）
if st.session_state.get("pending_redirect_t1", False):
    st.session_state["nav_selection"] = "📝 常规录入"
    st.session_state["pending_redirect_t1"] = False 

# 手机友好的功能下拉菜单导航
menu_options = [
    "📝 常规录入", 
    "📋 现货", 
    "🔮 预售", 
    "📦 包裹合拼与运费",  
    "🚚 发货看板",        
    "📊 月度营收统计"
]

selected_tab = st.selectbox("📌 请选择功能页面", menu_options, key="nav_selection", label_visibility="collapsed")

# 确保这里的判断文本和上面的列表一字不差
tab1 = (selected_tab == "📝 常规录入")
tab2 = (selected_tab == "📋 现货") 
tab3 = (selected_tab == "🔮 预售")
tab5 = (selected_tab == "📦 包裹合拼与运费") 
tab4 = (selected_tab == "🚚 发货看板")       
tab6 = (selected_tab == "📊 月度营收统计")

# ==================== TAB 1: 常规单笔录入 ====================
if tab1:
    st.markdown("##### 📝 录入买家买书需求 ")
    st.write("") 

    # 0. 初始化 session_state
    for k, default_val in [
        ("t1_buyer", ""), 
        ("t1_xianyu", ""), 
        ("t1_price_editable", 0.0)
    ]:
        if k not in st.session_state:
            st.session_state[k] = default_val

    if st.session_state.get("should_clear_t1", False):
        st.session_state["t1_buyer"] = ""
        st.session_state["t1_xianyu"] = ""
        st.session_state["t1_price_editable"] = 0.0
        st.session_state["should_clear_t1"] = False



    # --- 👇 表单录入核心区 ---
    st.write("---")
    st.markdown("#### 👤 订单基础信息")
    
    existing_books = []
    book_default_cutoff = {}
    book_default_shipping = {}


    if not df.empty and "book_name" in df.columns:
        for _, row in df.iterrows():
            b_raw = str(row.get("book_name", ""))
            if b_raw and b_raw != "nan":
                base_name = b_raw.split("（")[0].split("(")[0].strip()
                if base_name:
                    existing_books.append(base_name)

                    cutoff_val = row.get("official_cutoff_time")
                    shipping_val = row.get("official_shipping_time")
                    if pd.notna(cutoff_val) and cutoff_val:
                        book_default_cutoff[base_name] = cutoff_val
                    if pd.notna(shipping_val) and shipping_val:
                        book_default_shipping[base_name] = shipping_val


        existing_books = sorted(set(existing_books))

    c1, c2, c3 = st.columns(3)
    with c1:
        buyer = st.text_input("1. 买家账号", key="t1_buyer")
        xianyu = st.text_input("2. 闲鱼单号 (选填)", key="t1_xianyu")
        p_sell_total = st.number_input("3. 买家付款总额 (¥)", value=float(st.session_state.get("t1_price_editable", 0.0)), min_value=0.0, format="%.2f")
        
    with c2:
        shop = st.selectbox("4. 下单店铺", SHOPS, key="t1_shop")
        status = st.selectbox("5. 当前订单状态", STATUSES, key="t1_status")
        stock_type = st.radio("6. 商品属性", ["现货", "预售"], index=0, horizontal=True, key="t1_stock_type")
        
    with c3:
        default_d = st.session_state.get("t1_date", datetime.date.today())
        default_t = st.session_state.get("t1_time", datetime.datetime.now().time())
        input_date = st.date_input("7. 买家下单日期", value=default_d)
        input_time = st.time_input("8. 买家下单时间", value=default_t)
        auto_deadline = input_date + datetime.timedelta(days=15)
        st.info(f"⏰ 发货截止: **{auto_deadline.strftime('%Y-%m-%d')}**")

    st.markdown("---")
    st.markdown("#### 📚 书籍明细录入 (系统会自动将总金额平摊到下方每本书)")
    
    num_books = st.number_input("🛒 本次订单包含几本书？", min_value=1, max_value=20, value=1, help="增加数量可以一次性录入多本书，彻底告别 A+B")
    
    book_entries = []
    for i in range(int(num_books)):
        st.markdown(f"**第 {i+1} 本书：**")
        bc1, bc2, bc3 = st.columns([2, 2, 1])
        with bc1:
            sel_hist = st.selectbox("从历史书名中选择", ["-- 手动输入新书名 --"] + existing_books, key=f"t1_hist_{i}")
        with bc2:
            man_book = st.text_input("或手动输入新书名", key=f"t1_man_{i}", disabled=(sel_hist != "-- 手动输入新书名 --"))
        with bc3:
            edition = st.selectbox("版本", ["官网特", "A店特", "特装", "普装"], index=3, key=f"t1_ed_{i}")
            
        real_name = sel_hist if sel_hist != "-- 手动输入新书名 --" else man_book.strip()
        book_entries.append({"name": real_name, "edition": edition})

    official_cutoff = ""
    official_shipping = ""
    if stock_type == "预售":
        st.markdown("---")
        st.warning("🔮 **预售专属信息**：将同步应用于本次录入的所有预售书籍")
        
        default_cutoff_date = datetime.date.today()
        default_shipping_date = datetime.date.today() + datetime.timedelta(days=30)
        
        first_book_name = book_entries[0]["name"]
        if first_book_name in book_default_cutoff:
            try: default_cutoff_date = pd.to_datetime(book_default_cutoff[first_book_name]).date()
            except: pass
        if first_book_name in book_default_shipping:
            try: default_shipping_date = pd.to_datetime(book_default_shipping[first_book_name]).date()
            except: pass
        
        pc1, pc2 = st.columns(2)
        with pc1:
            cutoff_date = st.date_input("官方截单日期", value=default_cutoff_date)
            official_cutoff = cutoff_date.isoformat()
        with pc2:
            shipping_date = st.date_input("预计官方发货日期", value=default_shipping_date)
            official_shipping = shipping_date.isoformat()

    st.markdown('<div id="top-anchor"></div>', unsafe_allow_html=True)
    
    if st.button("💾 一键拆分并保存所有订单明细", type="primary", use_container_width=True):
        if not buyer:
            st.error("❌ 请输入买家账号！")
        else:
            valid_books = [b for b in book_entries if b["name"]]
            
            if not valid_books:
                st.error("❌ 请至少完整录入一本书的书名！")
            else:
                split_price = p_sell_total / len(valid_books)
                combined_datetime = datetime.datetime.combine(input_date, input_time).isoformat()
                
                order_rows = []
                for b in valid_books:
                    final_book_name = f"{b['name']}（{b['edition']}）"
                    order_rows.append({
                        "buyer_name": buyer,
                        "xianyu_no": xianyu,
                        "book_name": final_book_name,
                        "shop_name": shop,
                        "status": status,
                        "price_sell": split_price,
                        "price_buy": 0.0,
                        "purchase_type": "合并拼单",
                        "order_time": combined_datetime,
                        "stock_type": stock_type,
                        "deadline": auto_deadline.isoformat(),
                        "official_cutoff_time": official_cutoff,
                        "official_shipping_time": official_shipping
                    })

                with st.spinner("正在保存订单..."):
                    supabase.table("orders").insert(order_rows).execute()

                st.session_state["should_clear_t1"] = True
                st.success(f"✅ 成功录入买家【{buyer}】的 {len(valid_books)} 本书！总付款 ¥{p_sell_total} 已自动平摊。")
                st.session_state["pending_redirect_t1"] = True
                
                # 强制清理缓存刷新页面
                load_data.clear()
                st.rerun()
if tab2:
    st.markdown("### ⏳ 现货等待下单区")
    st.info("💡 显示所有属性为【现货】且状态为【买家已下单】的订单。在此多选并填写总成本后一键变更为【我方已下单】。")
    
    if not df.empty:
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

# ====== TAB 4: 自动发货与取件码汇总 (支持聚合显示买家所有不同的闲鱼单号) ======
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
                
                # 提取并去重该买家名下的所有不同闲鱼单号
                all_xianyu_nos = [str(x).strip() for x in group["xianyu_no"].tolist() if x and str(x).strip() and str(x).strip() != "nan"]
                unique_xianyu_nos = sorted(list(set(all_xianyu_nos)))
                xianyu_display_str = " / ".join(unique_xianyu_nos) if unique_xianyu_nos else "无"
                
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
                            st.markdown(f"🏷️ **关联闲鱼单号**：`{xianyu_display_str}`")
                            
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
