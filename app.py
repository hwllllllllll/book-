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

# ==================== 手机超友好的 6 大功能下拉菜单导航 ====================
menu_options = [
    "📝 常规录入", 
    "📋 现货等待下单",  # 👈 修改这里
    "🔮 预售管理", 
    "🚚 发货看板", 
    "📦 包裹合拼与运费", 
    "📊 月度营收统计"
]

selected_tab = st.selectbox("📌 请选择功能页面", menu_options, label_visibility="collapsed")
st.write("---")

tab1 = (selected_tab == "📝 常规录入")
tab2 = (selected_tab == "📋 现货等待下单") 
tab3 = (selected_tab == "🔮 预售管理")
tab4 = (selected_tab == "🚚 发货看板")
tab5 = (selected_tab == "📦 包裹合拼与运费")
tab6 = (selected_tab == "📊 月度营收统计")

# ==================== TAB 1: 常规单笔录入 (顶部自带截图自动识别填单 + 互斥联动) ====================
if tab1:
    st.markdown("##### 📝 录入买家买书需求 (默认合拼订单)")
    st.write("") 

    # 0. 初始化 session_state 默认值
    for k, default_val in [("t1_buyer", ""), ("t1_xianyu", ""), ("t1_manual_book", ""), ("t1_history_book", "-- 手动输入新书名 / 或从下方选择 --"), ("t1_price_editable", 0.0)]:
        if k not in st.session_state:
            st.session_state[k] = default_val

    if st.session_state.get("should_clear_t1", False):
        st.session_state["t1_buyer"] = ""
        st.session_state["t1_xianyu"] = ""
        st.session_state["t1_manual_book"] = ""
        st.session_state["t1_history_book"] = "-- 手动输入新书名 / 或从下方选择 --"
        st.session_state["t1_price_editable"] = 0.0
        st.session_state["should_clear_t1"] = False


# ==================== 📸 顶部：闲鱼截图智能识别 (图像预处理增强版) ====================
    with st.container():
        st.markdown("##### 📸 闲鱼截图智能识别 (AI 图像增强 + OCR 自动提取)")
        uploaded_screenshot = st.file_uploader("上传闲鱼订单截图", type=["jpg", "jpeg", "png"], key="auto_screenshot_input")
        
        if uploaded_screenshot is not None:
            st.image(uploaded_screenshot, width=200, caption="已上传待识别截图")
            if st.button("✨ 开始图像增强与智能识别", type="primary", key="parse_img_btn"):
                try:
                    import pytesseract
                    from PIL import Image, ImageEnhance, ImageFilter
                    import re

                    # 1. 读取原图
                    orig_image = Image.open(uploaded_screenshot)
                    
                    # ==================== 💡 核心：图像预处理（大幅提升 OCR 识别率） ====================
                    # ① 转为灰度图
                    gray_img = orig_image.convert('L')
                    
                    # ② 放大 2 倍（双三次插值，让文字边缘更清晰）
                    w, h = gray_img.size
                    resized_img = gray_img.resize((w * 2, h * 2), Image.Resampling.BICUBIC)
                    
                    # ③ 增强对比度
                    enhancer = ImageEnhance.Contrast(resized_img)
                    contrast_img = enhancer.enhance(2.0) # 提高对比度
                    
                    # ④ 锐化处理
                    sharpened_img = contrast_img.filter(ImageFilter.SHARPEN)
                    
                    # ⑤ 二值化（黑白化处理，过滤背景阴影和浅色干扰）
                    # 阈值设为 160，高于此的变白，低于此的变黑
                    threshold = 160
                    processed_img = sharpened_img.point(lambda p: 255 if p > threshold else 0)
                    # ======================================================================

                    # 运行 OCR 识别（使用处理后的高质量黑白大图）
                    extracted_text = pytesseract.image_to_string(processed_img, lang='chi_sim+eng')
                    
                    # 💡 展开查看增强后的 OCR 实际识别文本
                    with st.expander("🔍 点击查看增强 OCR 原始识别文本 (Debug)"):
                        st.text(extracted_text)

                    # 2. 强力提取买家昵称（兼容各种前后缀及换行）
                    detected_buyer = ""
                    buyer_match = re.search(r'买家昵称\s*[:：]?\s*([^\n\r]+)', extracted_text)
                    if buyer_match:
                        detected_buyer = buyer_match.group(1).strip()
                    else:
                        lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                        for i, line in enumerate(lines):
                            if "买家" in line or "昵称" in line:
                                cleaned_line = line.replace("买家昵称", "").replace("昵称", "").replace("买家", "").strip()
                                cleaned_line = re.sub(r'^[:：\s]+', '', cleaned_line)
                                if cleaned_line:
                                    detected_buyer = cleaned_line
                                    break
                                elif i + 1 < len(lines):
                                    detected_buyer = lines[i + 1]
                                    break

                    # 3. 智能提取书名（支持中英文、过滤杂质）
                    detected_book = ""
                    lines = [line.strip() for line in extracted_text.splitlines() if line.strip()]
                    for line in lines:
                        if not any(kw in line for kw in ['买家', '订单编号', '付款时间', '下单时间', '商品总价', '运费', '成交价', '交易快照', '支付宝', '地址', '去发货', '编号']):
                            if len(line) >= 2 and any(c.isalnum() or ('\u4e00' <= c <= '\u9fff') for c in line):
                                clean_line = re.sub(r'【.*?】', '', line).strip()
                                if clean_line and len(clean_line) > 2 and '¥' not in clean_line and not clean_line.isdigit():
                                    base_detected = clean_line.split("特装")[0].split("普装")[0].split("明信片")[0].strip()
                                    if len(base_detected) >= 2:
                                        detected_book = base_detected
                                        break
                    
                    # 常用英文书名兜底
                    if not detected_book:
                        for line in lines:
                            if any(k in line.lower() for k in ["flashlight", "moral", "1+2", "青春报告"]):
                                clean_line = re.sub(r'【.*?】', '', line).strip()
                                detected_book = clean_line.split("特装")[0].split("普装")[0].strip()
                                break

                    # 4. 提取下单时间
                    time_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{2}:\d{2}:\d{2})', extracted_text)
                    detected_datetime_str = time_match.group(1).strip() if time_match else ""
                    
                    # 5. 提取价格
                    prices = re.findall(r'[¥￥]\s*(\d+\.\d{2})', extracted_text)
                    detected_price = float(prices[0]) if prices else 0.0
                    
                    # 6. 强力提取闲鱼单号（全局精准过滤所有 15-22 位数字）
                    all_long_numbers = re.findall(r'\d{15,25}', extracted_text)
                    detected_xianyu = ""
                    if all_long_numbers:
                        valid_orders = [num for num in all_long_numbers if 15 <= len(num) <= 22]
                        if valid_orders:
                            detected_xianyu = valid_orders[0]
                        else:
                            detected_xianyu = all_long_numbers[0]

                    # 💡 写入状态自动回填到表单变量中
                    if detected_buyer:
                        st.session_state["t1_buyer"] = detected_buyer
                    if detected_price > 0:
                        st.session_state["t1_price_editable"] = detected_price
                    if detected_xianyu:
                        st.session_state["t1_xianyu"] = detected_xianyu
                    if detected_book:
                        st.session_state["t1_manual_book"] = detected_book
                        st.session_state["t1_history_book"] = "-- 手动输入新书名 / 或从下方选择 --"
                        
                    if detected_datetime_str:
                        try:
                            dt_obj = pd.to_datetime(detected_datetime_str)
                            st.session_state["t1_date"] = dt_obj.date()
                            st.session_state["t1_time"] = dt_obj.time()
                        except:
                            pass
                        
                    st.success(f"🎉 增强识别成功！\n- 买家昵称: {detected_buyer or '未识别'}\n- 书名: {detected_book or '未识别'}\n- 价格: ¥{detected_price}\n- 单号: {detected_xianyu or '未识别'}\n- 时间: {detected_datetime_str or '未识别'}")
                    import time
                    time.sleep(0.8)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 识别失败，错误信息: {e}")

    # 区分现货或预售
    stock_type = st.radio("📦 商品属性", ["现货", "预售"], index=0, horizontal=True, key="t1_stock_type")
    st.write("---")
    
    # 📚 智能提取历史书名字典、完整版本价格映射、预售时间及图片
    existing_books = []
    book_default_cutoff = {}
    book_default_shipping = {}
    exact_book_price = {}       
    base_book_price = {}        
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
                        exact_book_price[b_raw] = float(p_val)          
                        base_book_price[base_name] = float(p_val)       
                        
                    if img_val and str(img_val).startswith("data:image"):
                        book_default_image[base_name] = img_val
                    cutoff_val = row.get("official_cutoff_time")
                    shipping_val = row.get("official_shipping_time")
                    if cutoff_val: book_default_cutoff[base_name] = cutoff_val
                    if shipping_val: book_default_shipping[base_name] = shipping_val
                    
        existing_books = sorted(list(set(existing_books)))
    
    # 🎯 互斥联动逻辑控制
    current_history = st.session_state.get("t1_history_book", "-- 手动输入新书名 / 或从下方选择 --")
    current_manual = st.session_state.get("t1_manual_book", "")

    if current_manual.strip():
        if current_history != "-- 手动输入新书名 / 或从下方选择 --":
            st.session_state["t1_history_book"] = "-- 手动输入新书名 / 或从下方选择 --"
            current_history = "-- 手动输入新书名 / 或从下方选择 --"

    # 🔴 待选择/未选择状态判定（上下皆空）
    is_unselected = (current_history == "-- 手动输入新书名 / 或从下方选择 --") and (not current_manual.strip())

    if is_unselected:
        st.markdown("""
            <style>
            div[data-baseweb="select"] > div { border: 2px solid #ff4b4b !important; background-color: #fff8f8; }
            input[aria-label*="手动输入/补充书名"] { border: 2px solid #ff4b4b !important; background-color: #fff8f8 !important; }
            </style>
        """, unsafe_allow_html=True)
        st.error("⚠️ 【必填提醒】请从下方历史下拉框选择一本书，或者在下方手动输入新书名！")

    c1, c2, c3 = st.columns(3)
    with c1:
        buyer = st.text_input("1. 买家账号", key="t1_buyer")
        xianyu = st.text_input("2. 闲鱼单号 (选填)", key="t1_xianyu")
        
        st.markdown("---")
        st.markdown("📖 **书名选择**")
        
        selected_history_book = st.selectbox(
            "从历史书名中快速选择 (点击下拉选择)", 
            ["-- 手动输入新书名 / 或从下方选择 --"] + existing_books,
            key="t1_history_book",
            disabled=bool(current_manual.strip())
        )
        
        manual_book = st.text_input(
            "或者手动输入/补充书名 (可填 A+B 合并)", 
            key="t1_manual_book",
            disabled=bool(selected_history_book != "-- 手动输入新书名 / 或从下方选择 --")
        )
        
        if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --":
            base_book = selected_history_book
            is_history_selected = True
        else:
            base_book = manual_book.split("（")[0].split("(")[0].strip() if manual_book else ""
            is_history_selected = False

    with c2:
        shop = st.selectbox("4. 下单店铺", SHOPS, key="t1_shop")
        status = st.selectbox("5. 当前订单状态", STATUSES, key="t1_status")
        
    with c3:
        input_date = st.date_input("7. 买家下单日期", value=datetime.date.today(), key="t1_date")
        input_time = st.time_input("8. 买家下单时间", value=datetime.datetime.now().time(), key="t1_time")
        
        auto_deadline = input_date + datetime.timedelta(days=15)
        st.info(f"⏰ 发货截止日期 (自动+15天): **{auto_deadline.strftime('%Y-%m-%d')}**")
        
        st.markdown("---")
        edition_choice = st.radio(
            "✨ 特装/版本选项",
            ["官网特", "A店特", "特装", "普装"],
            index=3,
            horizontal=True,
            key="t1_edition"
        )

    raw_base_name = selected_history_book if selected_history_book != "-- 手动输入新书名 / 或从下方选择 --" else base_book
    candidate_full_name = f"{raw_base_name}（{edition_choice}）" if raw_base_name else ""
    
    with c2:
        if candidate_full_name and candidate_full_name in exact_book_price:
            default_price = exact_book_price[candidate_full_name]
            p_sell = st.number_input(f"6. 买家下单总价 (营收 - 已同步【{edition_choice}】历史价格)", value=default_price, disabled=True, key="t1_price_locked")
            st.caption(f"🔒 已自动锁定该书【{edition_choice}】的历史同版本价格")
        elif raw_base_name and raw_base_name in base_book_price:
            default_price = base_book_price[raw_base_name]
            p_sell = st.number_input("6. 买家下单总价 (营收 - 检测到其他版本价格，可修改)", value=default_price, min_value=0.0, format="%.2f", key="t1_price_editable_with_default")
            st.caption(f"💡 提示：该书有其他版本历史价格，当前【{edition_choice}】可按需修改")
        else:
            p_sell = st.number_input("6. 买家下单总价 (营收)", value=0.0, min_value=0.0, format="%.2f", key="t1_price_editable")

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
    st.markdown('<div id="top-anchor"></div>', unsafe_allow_html=True)

    if st.button("💾 保存单笔订单", type="primary", key="t1_submit_btn"):
        if is_unselected:
            st.error("❌ 请先从历史书名中选择一本书，或在下方手动输入书名！")
        elif not buyer:
            st.error("❌ 请输入买家账号！")
        else:
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
            
            st.session_state["should_clear_t1"] = True
            
            st.success(f"✅ 成功保存买家【{buyer}】的订单【{final_book_name}】！表单已清空并重置。")
            
            import streamlit.components.v1 as components
            components.html("""
                <script>
                    setTimeout(function() {
                        window.parent.scrollTo({top: 0, behavior: 'smooth'});
                    }, 50);
                </script>
            """, height=0)
            
            import time
            time.sleep(0.8)
            st.rerun()
            
            
# ====== TAB 2: 现货等待下单 ======
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
                        
                        # 自动生成当前时间的包裹采购批次号
                        import datetime
                        package_batch = f"PKG-{datetime.datetime.now().strftime('%m%d-%H%M%S')}"
                        
                        for oid in target_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_cost,
                                "package_id": package_batch  # 把这批书绑定在这个包裹号里
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功将勾选的现货订单变更为【我方已下单】！已生成包裹批次号：{package_batch}")
                        import time
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.success("🎉 目前没有需要去下单的现货订单！")
    else:
        st.info("暂无数据。")

# ====== TAB 3: 预售专区 (预售订单全流程跟踪) ======
if tab3:
    st.markdown("### 🔮 预售全流程管理")
    
    # ==================== 第一阶段：待下单 ====================
    st.subheader("🛒 第一阶段：待下单汇总")
    st.info("💡 系统已自动将相同书名的预售需求进行汇总。最前方会显示该书【还有多少本等待我去下单】，方便统一去采购！")
    
    if not df.empty:
        # 🎯 只筛选预售且状态为“买家已下单”的未采购需求
        presale_wait_df = df[(df["stock_type"] == "预售") & (df["status"] == "买家已下单")].copy()
        
        if not presale_wait_df.empty:
            group_cols = ["book_name", "official_cutoff_time", "official_shipping_time"]
            presale_summary = presale_wait_df.groupby(group_cols).agg(
                待下单数量=("id", "count"),
                买家列表=("buyer_name", lambda x: ", ".join(set(str(i) for i in x if i))),
                原始订单ids=("id", lambda x: list(x))
            ).reset_index()
            
            presale_summary = presale_summary.sort_values(by="待下单数量", ascending=False)
            presale_summary = presale_summary.rename(columns={
                "book_name": "📖 预售书名",
                "official_cutoff_time": "⏰ 官方截单时间",
                "official_shipping_time": "🚚 预计官方发货时间",
                "待下单数量": "🔥 还有几本待下单"
            })
            
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
                st.markdown(f"#### 🎯 已勾选了 **{len(selected_pre_rows)}** 款不同的预售书准备统一下单")
                
                with st.form("presale_batch_form"):
                    pre_total_cost = st.number_input("这批勾选预售书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入总价，系统会自动平摊")
                    
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary"):
                        all_target_ids = []
                        for _, row in selected_pre_rows.iterrows():
                            matched_idx = row.name
                            orig_ids = presale_summary.loc[matched_idx, "原始订单ids"]
                            all_target_ids.extend(orig_ids)
                        
                        split_pc = pre_total_cost / len(all_target_ids) if len(all_target_ids) > 0 else 0.0
                        
                        for oid in all_target_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_pc
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功更新为【我方已下单】！总成本已平摊（共涉及 {len(all_target_ids)} 个买家订单）。")
                        import time
                        time.sleep(0.5)
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
                        
                    for oid in all_ship_ids:
                        supabase.table("orders").update({
                            "status": "官方已发货" 
                        }).eq("id", int(oid)).execute()
                        
                    st.success(f"✅ 成功将勾选的预售书标记为【官方已发货】状态（共涉及 {len(all_ship_ids)} 个单子）！")
                    import time
                    time.sleep(0.5)
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
            
    st.info("💡 手机端优化版：每个买家一个独立卡片！自动汇总并展示该买家所有的不同闲鱼单号。")
    
    if not df.empty:
        arrived_buyers = df[(df["status"] == "已到货") & (df["buyer_name"] != "暂无")]["buyer_name"].unique()
        shipping_df = df[df["buyer_name"].isin(arrived_buyers)].copy()
        
        if not shipping_df.empty:
            for col in ["buyer_address", "pickup_area", "book_image", "xianyu_no", "deadline"]:
                if col not in shipping_df.columns:
                    shipping_df[col] = ""
                else:
                    shipping_df[col] = shipping_df[col].fillna("")

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
                has_unarrived = any(st_val != "已到货" for st_val in statuses)
                
                min_days = group["remaining_days"].min()
                total_sell = group["price_sell"].sum()
                
                # 🎯 核心升级：提取并去重该买家名下的所有不同闲鱼单号
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
                if has_unarrived:
                    card_title = f"🔴【有未到货】{card_title}"
                
                with st.expander(card_title, expanded=False):
                    if has_unarrived:
                        un_list = [f"{b} [{st}]" for b, st in zip(books, statuses) if st != "已到货"]
                        st.warning(f"⚠️ 注意：该买家名下有其他未到货商品：{' / '.join(un_list)}")
                    
                    st.markdown("##### 📖 购买书单明细：")
                    for idx, b_item in enumerate(books):
                        st.markdown(f"- **书本 {idx+1}**：{b_item}")
                        
                    images = [str(i) for i in group["book_image"] if i and str(i).startswith("data:image")]
                    if images:
                        st.markdown("##### 📸 书本照片：")
                        cols_img = st.columns(min(len(images), 4))
                        for i, img_data in enumerate(images):
                            with cols_img[i % 4]:
                                st.image(img_data, width=100)
                                
                    st.write("---")
                    
                    with st.form(key=f"form_shipping_{b_name}_{s_name}"):
                        f_col1, f_col2 = st.columns(2)
                        with f_col1:
                            new_addr = st.text_area("📍 收货地址", value=current_address, height=80)
                        with f_col2:
                            new_pickup = st.text_input("🏷️ 取件码", value=current_pickup)
                            # 🎯 在这里完整展示该买家的所有闲鱼单号
                            st.markdown(f"🏷️ **关联闲鱼单号**：`{xianyu_display_str}`")
                            
                        act_col1, act_col2 = st.columns(2)
                        with act_col1:
                            save_btn = st.form_submit_button("💾 保存此买家地址/取件码", type="secondary")
                        with act_col2:
                            ship_btn = st.form_submit_button("🚀 一键标记该买家【已发货】", type="primary")
                            
                        if save_btn:
                            for _, t_row in group.iterrows():
                                supabase.table("orders").update({
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }).eq("id", int(t_row["id"])).execute()
                            st.success(f"✅ 买家【{b_name}】的收货地址与取件码已更新！")
                            st.rerun()
                            
                        if ship_btn:
                            for _, t_row in group.iterrows():
                                supabase.table("orders").update({
                                    "status": "已发货",
                                    "buyer_address": new_addr,
                                    "pickup_area": new_pickup
                                }).eq("id", int(t_row["id"])).execute()
                            st.success(f"🚀 买家【{b_name}】的所有订单已成功标记为【已发货】并移出待发货区！")
                            import time
                            time.sleep(0.8)
                            st.rerun()
        else:
            st.info("📦 当前没有任何买家的包裹处于【已到货】状态。")
    else:
        st.info("暂无数据。")
        
# ====== TAB 5: 官方包裹合拼与海外邮费结算 ======
if tab5:
    st.markdown("### 📦 官方包裹合拼与海外邮费分摊")
    st.info("💡 这里显示的是你【统一下单】生成的采购包裹。当包裹发往你这边并产生海外运费时，录入总邮费，系统会自动平摊到该包裹内的每一本书。")
    
    if not df.empty and "package_id" in df.columns:
        # 🎯 筛选出有包裹号、且需要结算邮费的订单（通常是我方已下单或官方已发货状态）
        # 同时过滤掉那些 package_id 为空的数据
        pack_df = df[df["package_id"].notna() & (df["package_id"] != "")].copy()
        
        if not pack_df.empty:
            # 获取所有唯一的包裹批次号
            package_list = pack_df["package_id"].unique().tolist()
            
            selected_pkg = st.selectbox("📦 第一步：选择到达的【采购包裹批次】", ["-- 请选择包裹批次 --"] + package_list)
            
            if selected_pkg != "-- 请选择包裹批次 --":
                # 取出这个包裹里的所有书
                pkg_orders = pack_df[pack_df["package_id"] == selected_pkg]
                
                st.markdown(f"#### 🛍️ 包裹 **{selected_pkg}** 内共有 **{len(pkg_orders)}** 本书：")
                
                display_df = pkg_orders[["id", "buyer_name", "book_name", "status", "price_buy"]].rename(
                    columns={
                        "id": "订单号", 
                        "buyer_name": "所属买家", 
                        "book_name": "📖 书名", 
                        "status": "当前状态",
                        "price_buy": "单本采购价"
                    }
                )
                st.dataframe(display_df, hide_index=True, use_container_width=True)
                
                with st.form("freight_calc_form"):
                    total_freight = st.number_input("🚢 第二步：填写该包裹的【海外总邮费】(¥)", min_value=0.0, format="%.2f")
                    
                    if st.form_submit_button("⚡ 确认平摊海外邮费", type="primary"):
                        # 计算单本分摊邮费
                        split_freight = total_freight / len(pkg_orders) if len(pkg_orders) > 0 else 0.0
                        order_ids = pkg_orders["id"].tolist()
                        
                        for oid in order_ids:
                            supabase.table("orders").update({
                                "shipping_fee": split_freight,  # 👈 记录每本书分摊的海外邮费
                                "status": "已到达我方仓库"        # 👈 可选：更新状态为已到货，你可以改成你习惯的状态名
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 包裹 {selected_pkg} 的海外邮费已成功分摊！每本书均摊邮费: ¥{split_freight:.2f}")
                        import time
                        time.sleep(0.5)
                        st.rerun()
        else:
            st.success("🎉 目前没有待处理海外运费的包裹！")
    else:
        st.error("⚠️ 数据库中暂未检测到 `package_id` 字段，请确保已在 Supabase 中添加，并在下单区生成包裹。")


# ====== TAB 6: 月度营收统计 ======
if tab6:
    st.subheader("📊 月度营收与利润统计看板")
    st.info("💡 此页面按月份聚合展示你的订单营收、进货成本、国际运费以及预估净利润。")
    
    if not df.empty and "order_time" in df.columns:
        stats_df = df[df["buyer_name"] != "暂无"].copy()
        
        if not stats_df.empty:
            stats_df["month"] = pd.to_datetime(stats_df["order_time"], errors="coerce").dt.strftime("%Y-%m")
            stats_df["month"] = stats_df["month"].fillna("未知月份")
            
            for col in ["price_sell", "price_buy", "shipping_fee"]:
                if col not in stats_df.columns:
                    stats_df[col] = 0.0
                else:
                    stats_df[col] = stats_df[col].fillna(0.0)
                    
            monthly_summary = stats_df.groupby("month").agg(
                订单数=("id", "count"),
                总营收=("price_sell", "sum"),
                总进价=("price_buy", "sum"),
                总运费=("shipping_fee", "sum")
            ).reset_index()
            
            monthly_summary["总成本"] = monthly_summary["总进价"] + monthly_summary["总运费"]
            monthly_summary["净利润"] = monthly_summary["总营收"] - monthly_summary["总成本"]
            monthly_summary = monthly_summary.sort_values(by="month", ascending=False)
            
            tot_rev = monthly_summary["总营收"].sum()
            tot_cost = monthly_summary["总成本"].sum()
            tot_net = monthly_summary["净利润"].sum()
            
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.metric("💵 历史总营收", f"¥{tot_rev:.2f}")
            with m_col2:
                st.metric("🏷️ 历史总成本(进价+运费)", f"¥{tot_cost:.2f}")
            with m_col3:
                st.metric("📈 历史总净利润", f"¥{tot_net:.2f}", delta_color="normal" if tot_net >= 0 else "inverse")
                
            st.write("---")
            st.markdown("##### 📅 各月份详细账目清单：")
            
            display_monthly = monthly_summary.rename(columns={
                "month": "月份",
                "订单数": "订单书本数"
            })
            
            st.dataframe(
                display_monthly,
                column_config={
                    "总营收": st.column_config.NumberColumn("总营收 (¥)", format="¥%.2f"),
                    "总进价": st.column_config.NumberColumn("总进价 (¥)", format="¥%.2f"),
                    "总运费": st.column_config.NumberColumn("总运费 (¥)", format="¥%.2f"),
                    "总成本": st.column_config.NumberColumn("总成本 (¥)", format="¥%.2f"),
                    "净利润": st.column_config.NumberColumn("净利润 (¥)", format="¥%.2f"),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("📦 暂无统计数据。")
    else:
        st.info("暂无数据。")
