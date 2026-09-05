import streamlit as st
import pandas as pd
import base64
import datetime
from supabase import create_client, Client

# 初始化 Supabase 连接
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

SHOPS = ["大号", "小号"]
STATUSES = ["买家已下单", "我方已下单", "已合包", "已到货", "已发货"]

st.set_page_config(page_title="图书销售云后台", layout="wide")
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

# 读取云端数据（默认按下单时间：最早的排在最前面）
@st.cache_data(ttl=2) 
def load_data():
    response = supabase.table("orders").select("*").execute()
    df = pd.DataFrame(response.data)
    if not df.empty and "order_time" in df.columns:
        df["order_time"] = pd.to_datetime(df["order_time"], errors="coerce")
        df = df.sort_values(by="order_time", ascending=True) # 时间最早排前面
    return df

df = load_data()

# ---------------- 多功能选项卡 ----------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📝 常规单笔录入", 
    "⏳ 现货待下单区", 
    "🔮 预售专区 (截单/发货管理)", 
    "🚚 自动发货与取件码", 
    "📋 全部看板与月度统计"
])

# ====== TAB 1: 常规单笔录入 (保存后自动滚动回顶部、禁止下拉框打字) ======
with tab1:
    st.markdown("##### 📝 录入买家买书需求 (默认合拼订单)")
    st.write("") 
    
    # 0. 初始化 session_state 默认值并处理表单清空逻辑
    for k, default_val in [("t1_buyer", ""), ("t1_xianyu", ""), ("t1_manual_book", "")]:
        if k not in st.session_state:
            st.session_state[k] = default_val

    if st.session_state.get("should_clear_t1", False):
        st.session_state["t1_buyer"] = ""
        st.session_state["t1_xianyu"] = ""
        st.session_state["t1_manual_book"] = ""
        st.session_state["should_clear_t1"] = False

    # 区分现货或预售
    stock_type = st.radio("📦 商品属性", ["现货", "预售"], index=0, horizontal=True, key="t1_stock_type")
    st.write("---")
    
    # 📚 智能提取历史书名字典、价格、预售时间及图片
    existing_books = []
    book_default_cutoff = {}
    book_default_shipping = {}
    book_default_price = {}
    book_default_image = {}
    
    if not df.empty and "book_name" in df.columns:
        for _, row in df.iterrows():
            b_raw = str(row.get("book_name", ""))
            p_val = row.get("price_sell", 0.0)
            img_val = row.get("book_image", "")
            if b_raw and b_raw != "nan":
                base_name = b_raw.split("（")[0].split("(")[0].strip()
                if base_name:
                    existing_books.append(base_name)
                    if p_val and float(p_val) > 0:
                        book_default_price[base_name] = float(p_val)
                    if img_val and str(img_val).startswith("data:image"):
                        book_default_image[base_name] = img_val
                    cutoff_val = row.get("official_cutoff_time")
                    shipping_val = row.get("official_shipping_time")
                    if cutoff_val: book_default_cutoff[base_name] = cutoff_val
                    if shipping_val: book_default_shipping[base_name] = shipping_val
                    
        existing_books = sorted(list(set(existing_books)))
    
    c1, c2, c3 = st.columns(3)
    with c1:
        buyer = st.text_input("1. 买家账号", key="t1_buyer")
        xianyu = st.text_input("2. 闲鱼单号 (选填)", key="t1_xianyu")
        
        st.markdown("---")
        st.markdown("📖 **书名选择**")
        
        selected_history_book = st.selectbox(
            "从历史书名中快速选择 (点击下拉选择)", 
            ["-- 手动输入新书名 / 或从下方选择 --"] + existing_books,
            index=0,
            key="t1_history_book"
        )
        
        manual_book = st.text_input("或者手动输入/补充书名 (可填 A+B 合并)", key="t1_manual_book")
        
        if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --":
            base_book = selected_history_book
            is_history_selected = True
        else:
            base_book = manual_book.split("（")[0].split("(")[0].strip() if manual_book else ""
            is_history_selected = False

    with c2:
        shop = st.selectbox("4. 下单店铺", SHOPS, key="t1_shop")
        status = st.selectbox("5. 当前订单状态", STATUSES, key="t1_status")
        
        # 💰 价格联动逻辑
        lookup_key = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
        if lookup_key in book_default_price:
            default_price = book_default_price[lookup_key]
            p_sell = st.number_input("6. 买家下单总价 (营收 - 已自动同步历史价格)", value=default_price, disabled=True, key="t1_price_locked")
            st.caption("🔒 检测到同名书，价格已自动同步锁定")
        else:
            p_sell = st.number_input("6. 买家下单总价 (营收)", value=0.0, min_value=0.0, format="%.2f", key="t1_price_editable")
        
    with c3:
        input_date = st.date_input("7. 买家下单日期", value=datetime.date.today(), key="t1_date")
        input_time = st.time_input("8. 买家下单时间", value=datetime.datetime.now().time(), key="t1_time")
        
        # ⏰ 自动计算：发货截止日期 = 买家下单日期 + 15天
        auto_deadline = input_date + datetime.timedelta(days=15)
        st.info(f"⏰ 发货截止日期 (自动+15天): **{auto_deadline.strftime('%Y-%m-%d')}**")
        
        # ✨ 特装版本横向点选
        st.markdown("---")
        edition_choice = st.radio(
            "✨ 特装/版本选项",
            ["官网特", "A店特", "特装", "普装"],
            index=3,
            horizontal=True,
            key="t1_edition"
        )

    # 🔮 预售时间自动匹配
    official_cutoff = ""
    official_shipping = ""
    if stock_type == "预售":
        st.markdown("---")
        st.warning("🔮 **预售商品专属信息**：已自动同步同名书籍的历史截单与发货时间")
        
        default_cutoff_date = datetime.date.today()
        default_shipping_date = datetime.date.today() + datetime.timedelta(days=30)
        
        target_book_key = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
        
        if target_book_key in book_default_cutoff:
            try:
                default_cutoff_date = pd.to_datetime(book_default_cutoff[target_book_key]).date()
            except:
                pass
        if target_book_key in book_default_shipping:
            try:
                default_shipping_date = pd.to_datetime(book_default_shipping[target_book_key]).date()
            except:
                pass
        
        pc1, pc2 = st.columns(2)
        with pc1:
            cutoff_date = st.date_input("官方截单日期", value=default_cutoff_date, key="t1_cutoff")
            official_cutoff = cutoff_date.isoformat()
        with pc2:
            shipping_date = st.date_input("预计官方发货日期", value=default_shipping_date, key="t1_shipping")
            official_shipping = shipping_date.isoformat()

    st.write("---")
    uploaded_image = st.file_uploader("📸 上传书本真实照片 (留空则自动继承历史同款照片)", type=["jpg", "jpeg", "png"], key="book_upload_t1")
    
    # 📸 图片自动继承联动
    image_base64 = ""
    target_img_key = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
    
    if uploaded_image is not None:
        bytes_data = uploaded_image.getvalue()
        image_base64 = f"data:image/jpeg;base64,{base64.b64encode(bytes_data).decode()}"
        st.image(uploaded_image, width=120, caption="已上传新照片预览")
    elif target_img_key in book_default_image:
        image_base64 = book_default_image[target_img_key]
        st.success("🖼️ 已自动继承该书历史上传的真实照片")
    
    st.write("")
    if st.button("💾 保存单笔订单", type="primary", key="t1_submit_btn"):
        if buyer and (selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" or manual_book):
            real_base_name = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else manual_book.strip()
            final_book_name = f"{real_base_name}（{edition_choice}）"
            combined_datetime = datetime.datetime.combine(input_date, input_time).isoformat()
            
            supabase.table("orders").insert({
                "buyer_name": buyer,
                "xianyu_no": xianyu, 
                "book_name": final_book_name,
                "shop_name": shop,
                "status": status,
                "price_sell": p_sell,
                "price_buy": 0.0, 
                "book_image": image_base64,
                "purchase_type": "合并拼单",
                "order_time": combined_datetime,
                "stock_type": stock_type,
                "deadline": auto_deadline.isoformat(),
                "official_cutoff_time": official_cutoff,
                "official_shipping_time": official_shipping
            }).execute()
            
            # 💡 触发下次运行前的清空标记
            st.session_state["should_clear_t1"] = True
            
            # 🎯 自动平滑滚动回页面最顶部
            import streamlit.components.v1 as components
            components.html("<script>window.parent.scrollTo({top: 0, behavior: 'smooth'});</script>", height=0)
            
            st.success(f"✅ 成功保存买家【{buyer}】的订单【{final_book_name}】！表单已清空并已返回顶部。")
            
            import time
            time.sleep(0.8)
            st.rerun()
        else:
            st.error("❌ 请输入买家账号和选择/输入书名后再保存！")
            
# ====== TAB 2: 现货待下单区 (采购组包) ======
with tab2:
    st.subheader("⏳ 现货待下单区")
    st.info("💡 显示所有属性为【现货】且状态为【买家已下单】的订单。在此多选并填写总成本后一键变更为【我方已下单】。")
    
    if not df.empty:
        spot_wait_df = df[(df["status"] == "买家已下单") & (df["stock_type"] == "现货")].copy()
        
        if not spot_wait_df.empty:
            display_spot = spot_wait_df[["id", "buyer_name", "book_name", "price_sell", "deadline", "order_time"]].copy()
            display_spot.columns = ["订单编号", "买家账号", "书名", "买家下单价", "发货截止日期", "下单时间"]
            display_spot.insert(0, "选择下单", False)
            
            edited_spot = st.data_editor(
                display_spot,
                column_config={"选择下单": st.column_config.CheckboxColumn("勾选打包", default=False)},
                disabled=["订单编号", "买家账号", "书名", "买家下单价", "发货截止日期", "下单时间"],
                use_container_width=True,
                key="spot_wait_editor"
            )
            
            selected_spot = edited_spot[edited_spot["选择下单"] == True]
            if not selected_spot.empty:
                with st.form("spot_purchase_form"):
                    total_cost = st.number_input("这批现货的【我方总采购成本】", min_value=0.0, format="%.2f")
                    if st.form_submit_button("⚡ 确认现货已下单并平摊成本", type="primary"):
                        s_ids = selected_spot["订单编号"].tolist()
                        split_c = total_cost / len(s_ids) if len(s_ids) > 0 else 0.0
                        for oid in s_ids:
                            supabase.table("orders").update({"status": "我方已下单", "price_buy": split_c}).eq("id", int(oid)).execute()
                        st.success(f"✅ 成功更新 {len(s_ids)} 笔现货订单状态为【我方已下单】！")
                        st.rerun()
        else:
            st.success("🎉 当前没有等待下单的现货订单。")
    else:
        st.info("暂无数据。")

# ====== TAB 3: 预售专区 (相同书名汇总与待下单统计) ======
with tab3:
    st.subheader("🔮 预售专区 (同款预售书汇总与截单跟踪)")
    st.info("💡 系统已自动将相同书名的预售需求进行汇总。最前方会显示该书【还有多少本等待我去下单】，方便你统一去供应商处采购！")
    
    if not df.empty:
        # 🎯 只筛选预售且状态为“买家已下单”的未采购需求进行汇总展示
        presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].copy()
        
        if not presale_wait_df.empty:
            # 按书名、官方截单时间、预计发货时间进行分组汇总
            group_cols = ["book_name", "official_cutoff_time", "official_shipping_time"]
            
            presale_summary = presale_wait_df.groupby(group_cols).agg(
                待下单数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            # 按照待下单数量从多到少排序
            presale_summary = presale_summary.sort_values(by="待下单数量", ascending=False)
            
            # 重命名列
            presale_summary = presale_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 官方截单时间",
                "official_shipping_time": "🚚 预计官方发货时间",
                "待下单数量": "🔥 还有几本待下单"
            })
            
            # 在最前面插入勾选框
            presale_summary.insert(0, "选择下单", False)
            
            cols_order = ["选择下单", "🔥 还有几本待下单", "📖 预售书名", "⏰ 官方截单时间", "🚚 预计官方发货时间", "买家列表"]
            available_pre_cols = [c for c in cols_order if c in presale_summary.columns]
            
            edited_presale = st.data_editor(
                presale_summary[available_pre_cols],
                column_config={
                    "选择下单": st.column_config.CheckboxColumn("勾选该款", default=False),
                    "🔥 还有几本待下单": st.column_config.NumberColumn("🔥 待下单本数", format="%d 本")
                },
                disabled=["🔥 还有几本待下单", "📖 预售书名", "⏰ 官方截单时间", "🚚 预计官方发货时间", "买家列表"],
                use_container_width=True,
                key="presale_summary_editor"
            )
            
            selected_pre_rows = edited_presale[edited_presale["选择下单"] == True]
            
            if not selected_pre_rows.empty:
                st.markdown(f"### 🎯 已勾选了 **{len(selected_pre_rows)}** 款不同的预售书准备统一下单")
                
                with st.form("presale_batch_form"):
                    pre_total_cost = st.number_input("这批勾选预售书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入供应商账单总价，系统会自动平摊到这几本书的每个单子上")
                    
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary"):
                        # 收集所有选中的原始订单 ID
                        all_target_ids = []
                        for _, row in selected_pre_rows.iterrows():
                            # 通过行索引反查原始 ids
                            matched_idx = row.name
                            orig_ids = presale_summary.loc[matched_idx, "原始订单ids"]
                            all_target_ids.extend(orig_ids)
                        
                        split_pc = pre_total_cost / len(all_target_ids) if len(all_target_ids) > 0 else 0.0
                        
                        for oid in all_target_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_pc
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功将选中的预售书籍批量更新为【我方已下单】！总成本已平摊（共涉及 {len(all_target_ids)} 个买家订单）。")
                        st.rerun()
            else:
                st.warning("👆 请在上方的表格中勾选你本次在供应商处下单的预售款式。")
        else:
            st.success("🎉 太棒了！当前没有任何等待下单的预售订单。")
    else:
        st.info("暂无数据。")

# ====== TAB 4: 自动发货与取件码汇总 (修复变量名语法错误) ======
with tab4:
    sub_col1, sub_col2 = st.columns([3, 1])
    with sub_col1:
        st.subheader("🚚 待发货包裹自动汇总 (已到货区 & 未到货整行浅红高亮)")
    with sub_col2:
        if st.button("🔄 刷新发货数据", key="refresh_shipping"):
            st.rerun()
            
    st.info("💡 页面仅显示包含【已到货】商品的买家。若买家名下有其他【未到货】的书籍，整行会自动高亮为浅红色提醒！书单已拆分为多列独立格子展示。")
    
    if not df.empty:
        # 1. 找出所有状态为“已到货”的买家名称
        arrived_buyers = df[(df["status"] == "已到货") & (df["buyer_name"] != "暂无")]["buyer_name"].unique()
        
        # 2. 筛选出这些买家的所有订单
        shipping_df = df[df["buyer_name"].isin(arrived_buyers)].copy()
        
        if not shipping_df.empty:
            for col in ["buyer_address", "pickup_area", "book_image", "xianyu_no", "deadline"]:
                if col not in shipping_df.columns:
                    shipping_df[col] = ""
                else:
                    shipping_df[col] = shipping_df[col].fillna("")

            # 计算距离今天的剩余天数
            today_date = datetime.date.today()
            def calc_remaining_days(d_str):
                try:
                    if not d_str:
                        return 999
                    d_obj = pd.to_datetime(d_str).date()
                    delta = (d_obj - today_date).days
                    return delta
                except:
                    return 999

            shipping_df["remaining_days"] = shipping_df["deadline"].apply(calc_remaining_days)

            group_cols = [c for c in ["buyer_name", "shop_name"] if c in shipping_df.columns]
            
            # 🎯 核心逻辑：拆分成多列格子，并检查是否有未到货商品用于整行高亮
            processed_rows = []
            for name_key, group in shipping_df.groupby(group_cols):
                b_name = name_key[0]
                s_name = name_key[1] if len(name_key) > 1 else ""
                
                books = group["book_name"].tolist()
                statuses = group["status"].tolist()
                
                # 检查该买家名下是否包含任何“非已到货”的书籍（变量名已修正为下划线）
                has_unarrived = any(st_val != "已到货" for st_val in statuses)
                
                row_data = {
                    "buyer_name": b_name,
                    "shop_name": s_name,
                    "buyer_address": group["buyer_address"].iloc[0],
                    "pickup_area": group["pickup_area"].iloc[0],
                    "remaining_days": group["remaining_days"].min(),
                    "price_sell": group["price_sell"].sum(),
                    "xianyu_no": group["xianyu_no"].iloc[0],
                    "book_image": [str(i) for i in group["book_image"] if i and str(i).startswith("data:image")],
                    "has_unarrived": has_unarrived
                }
                
                # 动态填充前 3 本书到独立格子，超过 3 本的合并到第 3 个格子中
                for i in range(3):
                    if i < len(books):
                        row_data[f"📦 书本 {i+1}"] = books[i]
                    else:
                        row_data[f"📦 书本 {i+1}"] = ""
                        
                if len(books) > 3:
                    extra_books = " / ".join(books[3:])
                    row_data["📦 书本 3"] += f" (+ 更多: {extra_books})"
                    
                processed_rows.append(row_data)
                
            summary_df = pd.DataFrame(processed_rows)
            
            def format_days_text(days):
                if days == 999:
                    return "无限制"
                elif days < 0:
                    return f"🔴 已超期 {-days} 天"
                elif days == 0:
                    return "⚠️ 今天截止"
                elif days <= 5:
                    return f"🔥 仅剩 {days} 天"
                else:
                    return f"⏳ 剩 {days} 天"

            summary_df["⏰ 剩余发货时间"] = summary_df["remaining_days"].apply(format_days_text)
            summary_df = summary_df.sort_values(by="remaining_days", ascending=True)
            
            summary_df = summary_df.rename(columns={
                "buyer_name": "买家账号",
                "shop_name": "下单店铺",
                "buyer_address": "📍 收货地址",
                "pickup_area": "🏷️ 取件码",
                "price_sell": "总营收",
                "xianyu_no": "闲鱼单号"
            })
            
            cols_order = ["📦 书本 1", "📦 书本 2", "📦 书本 3", "📸 书本照片", "📍 收货地址", "🏷️ 取件码", "⏰ 剩余发货时间", "买家账号", "总营收", "下单店铺", "闲鱼单号"]
            available_cols = [c for c in cols_order if c in summary_df.columns]
            summary_df = summary_df[available_cols]

            # 🎨 样式高亮函数：如果该买家名下有未到货的书，整行背景变浅红色
            def highlight_unarrived(row):
                b_acc = row["买家账号"]
                original_info = next((r for r in processed_rows if r["buyer_name"] == b_acc), None)
                if original_info and original_info.get("has_unarrived"):
                    return ['background-color: #ffe6e6'] * len(row)
                return [''] * len(row)

            styled_summary = summary_df.style.apply(highlight_unarrived, axis=1)

            edited_summary = st.data_editor(
                styled_summary,
                column_config={
                    "📦 书本 1": st.column_config.TextColumn("📦 书本 1", width="medium"),
                    "📦 书本 2": st.column_config.TextColumn("📦 书本 2", width="medium"),
                    "📦 书本 3": st.column_config.TextColumn("📦 书本 3", width="medium"),
                    "📸 书本照片": st.column_config.ImageColumn("📸 照片", width="small"),
                    "📍 收货地址": st.column_config.TextColumn("📍 收货地址"),
                    "🏷️ 取件码": st.column_config.TextColumn("🏷️ 取件码"),
                    "⏰ 剩余发货时间": st.column_config.TextColumn("⏰ 发货倒计时")
                },
                disabled=["📦 书本 1", "📦 书本 2", "📦 书本 3", "📸 书本照片", "⏰ 剩余发货时间", "买家账号", "总营收", "下单店铺", "闲鱼单号"],
                use_container_width=True,
                key="shipping_editor"
            )
            
            if st.button("💾 保存发货区的修改（地址与取件码）", type="primary"):
                for idx, row in summary_df.iterrows():
                    b_name = row["买家账号"]
                    new_address = row["📍 收货地址"]
                    new_pickup = row["🏷️ 取件码"]
                    
                    target_rows = shipping_df[shipping_df["buyer_name"] == b_name]
                    
                    for _, t_row in target_rows.iterrows():
                        update_data = {}
                        if pd.notna(new_address): update_data["buyer_address"] = new_address
                        if pd.notna(new_pickup): update_data["pickup_area"] = new_pickup
                        if update_data:
                            supabase.table("orders").update(update_data).eq("id", int(t_row["id"])).execute()
                st.success("✅ 发货信息已同步保存！")
                st.rerun()
        else:
            st.info("📦 当前没有任何买家的包裹处于【已到货】状态。")
    else:
        st.info("暂无数据。")
        
# ====== TAB 5: 全部看板与月度统计 ======
with tab5:
    st.subheader("📋 全量明细看板与月度财务营收")
    
    if not df.empty:
        st.markdown("##### 🔄 快速更新订单状态")
        ec1, ec2, ec3 = st.columns([2, 2, 1])
        with ec1:
            order_list = df["id"].astype(str) + " - " + df["buyer_name"].astype(str) + " (" + df["book_name"].astype(str) + ")"
            selected_order = st.selectbox("选择订单", order_list)
        with ec2:
            new_status = st.selectbox("更改状态", STATUSES)
        with ec3:
            st.write("")
            st.write("")
            if st.button("更新状态"):
                target_id = int(selected_order.split(" - ")[0])
                supabase.table("orders").update({"status": new_status}).eq("id", target_id).execute()
                st.success("更新成功！")
                st.rerun()
                
        st.divider()
        st.markdown("### 📊 月度财务营收统计")
        stat_df = df.copy()
        stat_df["month_str"] = stat_df["order_time"].dt.strftime("%Y-%m").fillna("未知月份")
        
        monthly_summary = stat_df.groupby("month_str").agg(
            订单笔数=("id", "count"),
            总营收=("price_sell", "sum"),
            总成本=("price_buy", "sum")
        ).reset_index()
        monthly_summary["净利润"] = monthly_summary["总营收"] - monthly_summary["总成本"]
        monthly_summary = monthly_summary.sort_values(by="month_str", ascending=False)
        
        for _, row in monthly_summary.iterrows():
            m_str = row["month_str"]
            with st.expander(f"📂 【 {m_str} 月份 】 — 营收: ¥{row['总营收']:.2f} | 成本: ¥{row['总成本']:.2f} | 净利润: ¥{row['净利润']:.2f} (共 {row['订单笔数']} 笔)"):
                m_detail = stat_df[stat_df["month_str"] == m_str]
                st.dataframe(m_detail[["id", "buyer_name", "book_name", "stock_type", "status", "price_sell", "price_buy", "order_time"]], use_container_width=True, hide_index=True)
    else:
        st.info("暂无数据。")
