import streamlit as st
import pandas as pd
import base64
from supabase import create_client, Client

# 初始化 Supabase 连接
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

SHOPS = ["大号", "小号"]
STATUSES = ["买家已下单", "我方已下单", "已合包", "已到货", "已发货"]

st.set_page_config(page_title="图书销售云后台", layout="wide")
st.title("☁️ 图书销售商家后台 (Supabase 驱动)")

# 读取云端数据
@st.cache_data(ttl=2) 
def load_data():
    response = supabase.table("orders").select("*").execute()
    return pd.DataFrame(response.data)

df = load_data()

# ---------------- 多功能选项卡 ----------------
tab1, tab2, tab3, tab4 = st.tabs(["📝 常规单笔录入", "⏳ 等待下单区 (采购组包)", "🚚 自动发货与取件码汇总", "📋 全部订单看板"])

# ====== TAB 1: 常规单笔录入 (无成本，仅记买家需求) ======
with tab1:
    with st.form("new_order"):
        purchase_type = st.radio("📦 进货方式", ["独立下单", "合并拼单"], index=1, horizontal=True)
        st.write("") 
        
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
            # 💡 提示：这里已经彻底去掉了成本输入框，因为下单后才知价格！
            import datetime
            input_date = st.date_input("7. 买家下单日期", value=datetime.date.today())
            input_time = st.time_input("8. 买家下单时间", value=datetime.datetime.now().time())
            
        st.write("---")
        uploaded_image = st.file_uploader("📸 上传书本真实照片 (用于识别重复版本)", type=["jpg", "jpeg", "png"], key="book_upload")
        image_base64 = ""
        if uploaded_image is not None:
            bytes_data = uploaded_image.getvalue()
            image_base64 = f"data:image/jpeg;base64,{base64.b64encode(bytes_data).decode()}"
            st.image(uploaded_image, width=120, caption="已上传照片预览")
        
        if st.form_submit_button("💾 保存单笔订单 (初始成本记为0)"):
            if buyer and book:
                combined_datetime = datetime.datetime.combine(input_date, input_time).isoformat()
                
                supabase.table("orders").insert({
                    "buyer_name": buyer,
                    "xianyu_no": xianyu, 
                    "book_name": book,
                    "shop_name": shop,
                    "status": status,
                    "price_sell": p_sell,
                    "price_buy": 0.0, # 初始成本默认为0，等待后续在采购页统一套算
                    "book_image": image_base64,
                    "purchase_type": purchase_type,
                    "order_time": combined_datetime
                }).execute()
                st.success("订单已同步至云端！")
                st.rerun()
            else:
                st.error("请输入买家账号和书名。")

# ====== TAB 2: 等待下单区 (采购组包与“已下单”确认) ======
with tab2:
    st.subheader("⏳ 等待下单区 (买家需求池)")
    st.info("💡 这里显示所有**状态为【买家已下单】**的未采购需求。当你去供应商处统一采购后，在此**多选**对应的书，点击下方的批量操作按钮录入成本和下单时间，它们就会自动转为【我方已下单】状态！")
    
    if not df.empty:
        # 只筛选出等待下单的订单
        wait_df = df[df["status"] == "买家已下单"].copy()
        
        if not wait_df.empty:
            display_wait_df = wait_df[["id", "buyer_name", "book_name", "price_sell", "shop_name", "order_time"]].copy()
            display_wait_df.columns = ["订单编号", "买家账号", "书名", "买家下单价", "店铺", "买家下单时间"]
            display_wait_df.insert(0, "选择下单", False)
            
            edited_wait = st.data_editor(
                display_wait_df,
                column_config={
                    "选择下单": st.column_config.CheckboxColumn("勾选打包", default=False)
                },
                disabled=["订单编号", "买家账号", "书名", "买家下单价", "店铺", "买家下单时间"],
                use_container_width=True,
                key="wait_orders_editor"
            )
            
            selected_wait_rows = edited_wait[edited_wait["选择下单"] == True]
            
            if not selected_wait_rows.empty:
                st.markdown(f"### 🎯 已勾选了 **{len(selected_wait_rows)}** 笔订单准备统一确认下单")
                
                with st.form("confirm_purchase_form"):
                    col_p1, col_p2 = st.columns(2)
                    with col_p1:
                        import datetime
                        p_date = st.date_input("1. 我方实际下单日期", value=datetime.date.today())
                        p_time = st.time_input("2. 我方实际下单时间", value=datetime.datetime.now().time())
                    with col_p2:
                        total_batch_cost = st.number_input("3. 这批书的【我方总采购成本】", min_value=0.0, format="%.2f", help="输入供应商账单总价，系统会自动平摊到这几本书上")
                    
                    if st.form_submit_button("⚡ 点击【已下单】按钮：确认采购并平摊成本", type="primary"):
                        combined_p_datetime = datetime.datetime.combine(p_date, p_time).isoformat()
                        selected_ids = selected_wait_rows["订单编号"].tolist()
                        
                        split_cost = total_batch_cost / len(selected_ids) if len(selected_ids) > 0 else 0.0
                        
                        for oid in selected_ids:
                            supabase.table("orders").update({
                                "status": "我方已下单",
                                "price_buy": split_cost
                            }).eq("id", int(oid)).execute()
                            
                        st.success(f"✅ 成功确认下单 {len(selected_ids)} 笔订单！状态已更新为【我方已下单】，总成本已平摊（每单 ¥{split_cost:.2f}）。")
                        st.rerun()
            else:
                st.warning("👆 请在上方的表格中勾选你本次在供应商处下单的书籍。")
        else:
            st.success("🎉 太棒了！当前没有任何等待下单的买家需求。")
    else:
        st.info("暂无数据。")

# ====== TAB 3: 自动发货与取件码汇总 ======
with tab3:
    st.subheader("🚚 待发货包裹自动汇总与对齐")
    st.info("💡 最核心的【合拼书单、书本照片、收货地址、取件码、成本】已为您置顶。你可以直接在此补填/修改信息，核对照片后一键保存！")
    
    if not df.empty:
        shipping_df = df[df["buyer_name"] != "暂无"].copy()
        
        if not shipping_df.empty:
            for col in ["buyer_address", "pickup_area", "book_image", "xianyu_no", "price_buy"]:
                if col not in shipping_df.columns:
                    shipping_df[col] = 0.0 if col == "price_buy" else ""
                else:
                    if col == "price_buy":
                        shipping_df[col] = shipping_df[col].fillna(0.0)
                    else:
                        shipping_df[col] = shipping_df[col].fillna("")

            group_cols = [c for c in ["buyer_name", "shop_name", "status"] if c in shipping_df.columns]
            
            summary_df = shipping_df.groupby(group_cols).agg({
                "book_name": lambda x: "\n".join([f"• {str(i)}" for i in x if i]),
                "book_image": lambda x: [str(i) for i in x if i and str(i).startswith("data:image")],
                "buyer_address": "first",
                "pickup_area": "first",
                "price_sell": "sum",
                "price_buy": "sum",
                "xianyu_no": "first"
            }).reset_index()
            
            summary_df = summary_df.rename(columns={
                "book_name": "📦 合拼书单 (点击核对)",
                "book_image": "📸 书本照片核对",
                "buyer_address": "📍 收货地址 (点击可修改)",
                "pickup_area": "🏷️ 取件码 (点击可修改)",
                "price_buy": "💰 订单成本 (可补填)",
                "buyer_name": "买家账号",
                "shop_name": "下单店铺",
                "status": "当前状态",
                "price_sell": "总营收",
                "xianyu_no": "闲鱼单号"
            })
            
            cols_order = [
                "📦 合拼书单 (点击核对)", 
                "📸 书本照片核对", 
                "📍 收货地址 (点击可修改)", 
                "🏷️ 取件码 (点击可修改)", 
                "💰 订单成本 (可补填)",
                "买家账号", 
                "当前状态", 
                "总营收", 
                "下单店铺", 
                "闲鱼单号"
            ]
            available_cols = [c for c in cols_order if c in summary_df.columns]
            summary_df = summary_df[available_cols]

            edited_summary = st.data_editor(
                summary_df,
                column_config={
                    "📦 合拼书单 (点击核对)": st.column_config.TextColumn("📦 合拼书单 (分行显示)", width="large"),
                    "📸 书本照片核对": st.column_config.ImageColumn("📸 书本照片核对", width="medium"),
                    "📍 收货地址 (点击可修改)": st.column_config.TextColumn("📍 收货地址"),
                    "🏷️ 取件码 (点击可修改)": st.column_config.TextColumn("🏷️ 取件码"),
                    "💰 订单成本 (可补填)": st.column_config.NumberColumn("💰 订单成本", format="%.2f", min_value=0.0)
                },
                disabled=["📦 合拼书单 (点击核对)", "📸 书本照片核对", "买家账号", "当前状态", "总营收", "下单店铺", "闲鱼单号"],
                use_container_width=True,
                key="shipping_editor"
            )
            
            if st.button("💾 保存发货区的修改（地址、取件码、成本）", type="primary"):
                for idx, row in edited_summary.iterrows():
                    b_name = row["买家账号"]
                    new_address = row["📍 收货地址 (点击可修改)"]
                    new_pickup = row["🏷️ 取件码 (点击可修改)"]
                    new_cost = row["💰 订单成本 (可补填)"]
                    
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
                            
                st.success("✅ 发货信息及成本已成功同步保存至云端！")
                st.rerun()
        else:
            st.warning("当前没有检测到有具名买家的订单。")
    else:
        st.info("暂无数据。")

# ====== TAB 4: 全部订单看板 (状态修改与全量明细) ======
with tab4:
    st.subheader("📋 云端实时管理看板 (全部明细)")
    
    if not df.empty:
        df = df.sort_values(by="order_time", ascending=False)
        
        st.markdown("##### 快速更新单个订单状态")
        edit_col1, edit_col2, edit_col3 = st.columns([2, 2, 1])
        with edit_col1:
            order_list = df["id"].astype(str) + " - " + df["buyer_name"].astype(str) + " (" + df["book_name"].astype(str) + ")"
            selected_order = st.selectbox("选择要更新的订单", order_list)
        with edit_col2:
            new_status = st.selectbox("更改为新状态", STATUSES)
        with edit_col3:
            st.write("") 
            st.write("") 
            if st.button("🔄 更新状态"):
                target_id = int(selected_order.split(" - ")[0])
                supabase.table("orders").update({"status": new_status}).eq("id", target_id).execute()
                st.success("状态更新成功！")
                st.rerun()

        display_df = df.rename(columns={
            "id": "编号",
            "purchase_type": "进货方式",
            "buyer_name": "买家账号",
            "buyer_address": "买家地址",
            "pickup_area": "取件码",
            "xianyu_no": "闲鱼单号",
            "book_name": "书名",
            "book_image": "书本照片",
            "shop_name": "店铺",
            "status": "状态",
            "price_sell": "订单营收",
            "price_buy": "订单成本",
            "order_time": "下单时间"
        })
        
        available_cols = [c for c in ["编号", "进货方式", "买家账号", "买家地址", "取件码", "闲鱼单号", "书名", "书本照片", "店铺", "状态", "订单营收", "订单成本", "下单时间"] if c in display_df.columns]
        
        st.dataframe(
            display_df[available_cols], 
            column_config={
                "书本照片": st.column_config.ImageColumn("书本照片", width="small")
            },
            use_container_width=True, 
            hide_index=True
        )
    else:
        st.info("目前云端数据库还没有订单哦！")
