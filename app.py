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
st.title("☁️ 图书销售商家后台 (现货与预售多维管理)")

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

# ====== TAB 1: 常规单笔录入 (截止时间自动计算为下单后30天) ======
with tab1:
    with st.form("new_order"):
        st.markdown("##### 📝 录入买家买书需求 (默认合拼订单)")
        st.write("") 
        
        # 区分现货或预售
        stock_type = st.radio("📦 商品属性", ["现货", "预售"], index=0, horizontal=True)
        st.write("---")
        
        c1, c2, c3 = st.columns(3)
        with c1:
            buyer = st.text_input("1. 买家账号")
            xianyu = st.text_input("2. 闲鱼单号 (选填)")
            book = st.text_input("3. 书名 (可填 A+B 合并)")
        with c2:
            shop = st.selectbox("4. 下单店铺", SHOPS)
            status = st.selectbox("5. 当前订单状态", STATUSES)
            p_sell = st.number_input("6. 买家下单总价(营收)", min_value=0.0)
        with c3:
            input_date = st.date_input("7. 买家下单日期", value=datetime.date.today())
            input_time = st.time_input("8. 买家下单时间", value=datetime.datetime.now().time())
            
            # ⏰ 自动计算：发货截止日期 = 买家下单日期 + 30天（只显示日期）
            auto_deadline = input_date + datetime.timedelta(days=30)
            st.info(f"⏰ 发货截止日期 (自动+30天): **{auto_deadline.strftime('%Y-%m-%d')}**")

        # 🔮 如果选择“预售”，额外展示官方截单时间与预计官方发货时间
        official_cutoff = ""
        official_shipping = ""
        if stock_type == "预售":
            st.markdown("---")
            st.warning("🔮 **预售商品专属信息**：请填写官方截单与预计发货时间")
            pc1, pc2 = st.columns(2)
            with pc1:
                cutoff_date = st.date_input("官方截单日期", value=datetime.date.today())
                official_cutoff = cutoff_date.isoformat()
            with pc2:
                shipping_date = st.date_input("预计官方发货日期", value=datetime.date.today() + datetime.timedelta(days=30))
                official_shipping = shipping_date.isoformat()

        st.write("---")
        uploaded_image = st.file_uploader("📸 上传书本真实照片", type=["jpg", "jpeg", "png"], key="book_upload")
        image_base64 = ""
        if uploaded_image is not None:
            bytes_data = uploaded_image.getvalue()
            image_base64 = f"data:image/jpeg;base64,{base64.b64encode(bytes_data).decode()}"
            st.image(uploaded_image, width=120, caption="已上传照片预览")
        
        if st.form_submit_button("💾 保存单笔订单"):
            if buyer and book:
                combined_datetime = datetime.datetime.combine(input_date, input_time).isoformat()
                
                supabase.table("orders").insert({
                    "buyer_name": buyer,
                    "xianyu_no": xianyu, 
                    "book_name": book,
                    "shop_name": shop,
                    "status": status,
                    "price_sell": p_sell,
                    "price_buy": 0.0, 
                    "book_image": image_base64,
                    "purchase_type": "合并拼单",
                    "order_time": combined_datetime,
                    "stock_type": stock_type,
                    "deadline": auto_deadline.isoformat(), # 自动存入计算好的 30 天后日期
                    "official_cutoff_time": official_cutoff,
                    "official_shipping_time": official_shipping
                }).execute()
                st.success("订单已成功同步至云端！")
                st.rerun()
            else:
                st.error("请输入买家账号和书名。")

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

# ====== TAB 3: 预售专区 (截单管理与批量下单) ======
with tab3:
    st.subheader("🔮 预售专区 (官方截单与预计发货跟踪)")
    st.info("💡 在这里集中查看所有【预售】订单的**官方截单时间**与**预计官方发货时间**，并进行预售订单的批量采购下单。")
    
    if not df.empty:
        presale_df = df[df["stock_type"] == "预售"].copy()
        if not presale_df.empty:
            display_pre = presale_df[["id", "buyer_name", "book_name", "official_cutoff_time", "official_shipping_time", "status", "order_time"]].copy()
            display_pre.columns = ["订单编号", "买家账号", "书名", "官方截单时间", "预计官方发货时间", "当前状态", "下单时间"]
            display_pre.insert(0, "选择下单", False)
            
            edited_pre = st.data_editor(
                display_pre,
                column_config={"选择下单": st.column_config.CheckboxColumn("勾选预售打包", default=False)},
                disabled=["订单编号", "买家账号", "书名", "官方截单时间", "预计官方发货时间", "当前状态", "下单时间"],
                use_container_width=True,
                key="presale_editor"
            )
            
            selected_pre = edited_pre[edited_pre["选择下单"] == True]
            if not selected_pre.empty:
                with st.form("presale_purchase_form"):
                    pre_total_cost = st.number_input("这批预售书的【我方总采购成本】", min_value=0.0, format="%.2f")
                    if st.form_submit_button("⚡ 确认预售已下单并平摊成本", type="primary"):
                        p_ids = selected_pre["订单编号"].tolist()
                        split_pc = pre_total_cost / len(p_ids) if len(p_ids) > 0 else 0.0
                        for oid in p_ids:
                            supabase.table("orders").update({"status": "我方已下单", "price_buy": split_pc}).eq("id", int(oid)).execute()
                        st.success(f"✅ 成功更新 {len(p_ids)} 笔预售订单为【我方已下单】！")
                        st.rerun()
        else:
            st.info("当前没有预售订单记录。")
    else:
        st.info("暂无数据。")

# ====== TAB 4: 自动发货与取件码汇总 ======
with tab4:
    st.subheader("🚚 待发货包裹自动汇总 (按下单时间排序)")
    st.info("💡 核心对齐区：合拼书单、照片、收货地址、取件码、截止时间。")
    
    if not df.empty:
        shipping_df = df[df["buyer_name"] != "暂无"].copy()
        if not shipping_df.empty:
            for col in ["buyer_address", "pickup_area", "book_image", "xianyu_no", "price_buy", "deadline"]:
                if col not in shipping_df.columns:
                    shipping_df[col] = 0.0 if col == "price_buy" else ""
                else:
                    shipping_df[col] = shipping_df[col].fillna(0.0 if col == "price_buy" else "")

            group_cols = [c for c in ["buyer_name", "shop_name", "status"] if c in shipping_df.columns]
            
            summary_df = shipping_df.groupby(group_cols).agg({
                "book_name": lambda x: "\n".join([f"• {str(i)}" for i in x if i]),
                "book_image": lambda x: [str(i) for i in x if i and str(i).startswith("data:image")],
                "buyer_address": "first",
                "pickup_area": "first",
                "deadline": "min", # 取最早的截止时间
                "price_sell": "sum",
                "price_buy": "sum",
                "xianyu_no": "first"
            }).reset_index()
            
            summary_df = summary_df.rename(columns={
                "book_name": "📦 合拼书单",
                "book_image": "📸 书本照片",
                "buyer_address": "📍 收货地址",
                "pickup_area": "🏷️ 取件码",
                "deadline": "⏰ 截止发货日期",
                "price_buy": "💰 订单成本",
                "buyer_name": "买家账号",
                "shop_name": "下单店铺",
                "status": "当前状态",
                "price_sell": "总营收",
                "xianyu_no": "闲鱼单号"
            })
            
            cols_order = ["📦 合拼书单", "📸 书本照片", "📍 收货地址", "🏷️ 取件码", "⏰ 截止发货日期", "💰 订单成本", "买家账号", "当前状态", "总营收", "下单店铺", "闲鱼单号"]
            available_cols = [c for c in cols_order if c in summary_df.columns]
            summary_df = summary_df[available_cols]

            edited_summary = st.data_editor(
                summary_df,
                column_config={
                    "📦 合拼书单": st.column_config.TextColumn("📦 合拼书单", width="large"),
                    "📸 书本照片": st.column_config.ImageColumn("📸 照片", width="medium"),
                    "📍 收货地址": st.column_config.TextColumn("📍 收货地址"),
                    "🏷️ 取件码": st.column_config.TextColumn("🏷️ 取件码"),
                    "💰 订单成本": st.column_config.NumberColumn("💰 订单成本", format="%.2f")
                },
                disabled=["📦 合拼书单", "📸 书本照片", "买家账号", "当前状态", "总营收", "下单店铺", "闲鱼单号"],
                use_container_width=True,
                key="shipping_editor"
            )
            
            if st.button("💾 保存发货区的修改", type="primary"):
                for idx, row in edited_summary.iterrows():
                    b_name = row["买家账号"]
                    new_address = row["📍 收货地址"]
                    new_pickup = row["🏷️ 取件码"]
                    new_cost = row["💰 订单成本"]
                    
                    target_rows = shipping_df[shipping_df["buyer_name"] == b_name]
                    num_items = len(target_rows)
                    split_cost = new_cost / num_items if num_items > 0 else new_cost
                    
                    for _, t_row in target_rows.iterrows():
                        update_data = {}
                        if pd.notna(new_address): update_data["buyer_address"] = new_address
                        if pd.notna(new_pickup): update_data["pickup_area"] = new_pickup
                        if pd.notna(new_cost): update_data["price_buy"] = split_cost
                        if update_data:
                            supabase.table("orders").update(update_data).eq("id", int(t_row["id"])).execute()
                st.success("✅ 发货信息已同步保存！")
                st.rerun()

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
