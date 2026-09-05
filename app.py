import streamlit as st
import pandas as pd
from supabase import create_client, Client

# 初始化 Supabase 连接
url = st.secrets["SUPABASE_URL"]
key = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(url, key)

SHOPS = ["大号", "小号"]
STATUSES = ["买家已下单", "我方已下单", "已合包", "已到货", "准备发货", "已发货"]

st.set_page_config(page_title="图书销售云后台", layout="wide")
st.title("☁️ 图书销售商家后台 (Supabase 驱动)")

# 读取云端数据
@st.cache_data(ttl=2) 
def load_data():
    response = supabase.table("orders").select("*").execute()
    return pd.DataFrame(response.data)

df = load_data()

# ---------------- 多功能选项卡 ----------------
tab1, tab2, tab3 = st.tabs(["📝 常规单笔录入", "📦 批量拼单录入", "🚚 自动发货汇总"])

# ====== TAB 1: 常规单笔录入 ======
with tab1:
    with st.form("new_order"):
        purchase_type = st.radio("📦 进货方式", ["独立下单", "合并拼单"], index=1, horizontal=True)
        st.write("") 
        
        c1, c2, c3 = st.columns(3)
        with c1:
            buyer = st.text_input("1. 买家账号")
            address = st.text_input("2. 买家收货地址")
            pickup_area = st.text_input("3. 取件码地区")
        with c2:
            xianyu = st.text_input("4. 闲鱼单号 (选填)")
            book = st.text_input("5. 书名 (可填 A+B 合并)")
            shop = st.selectbox("6. 下单店铺", SHOPS)
        with c3:
            status = st.selectbox("7. 当前订单状态", STATUSES)
            p_sell = st.number_input("8. 买家下单总价(营收)", min_value=0.0)
            p_buy = st.number_input("9. 我方总成本价(成本)", min_value=0.0)
            image_info = st.text_input("10. 书本图片链接或说明", help="方便识别重复书籍或版本")
        
        if st.form_submit_button("💾 保存单笔订单"):
            if buyer and book:
                supabase.table("orders").insert({
                    "buyer_name": buyer,
                    "buyer_address": address,
                    "pickup_area": pickup_area,
                    "xianyu_no": xianyu, 
                    "book_name": book,
                    "shop_name": shop,
                    "status": status,
                    "price_sell": p_sell,
                    "price_buy": p_buy,
                    "book_image": image_info,
                    "purchase_type": purchase_type 
                }).execute()
                st.success("订单已同步至云端！")
                st.rerun()
            else:
                st.error("请输入买家账号和书名。")

# ====== TAB 2: 批量拼单录入 (自动平摊成本) ======
with tab2:
    st.info("💡 **使用说明**：当你在供应商处支付了一笔总花费（包含多本书）时，在下方填入总价并列出书籍。系统会自动帮你平摊成本。")
    
    batch_total_cost = st.number_input("💰 这批拼单的 **我方总花费 (成本)**", min_value=0.0, format="%.2f")
    
    init_data = [{"买家账号": "暂无", "买家地址": "", "取件码地区": "", "书名": "", "买家下单价(营收)": 0.0, "下单店铺": "大号", "闲鱼单号(选填)": "", "图片备注": ""} for _ in range(3)]
    batch_df = pd.DataFrame(init_data)
    
    edited_batch = st.data_editor(
        batch_df, 
        num_rows="dynamic", 
        column_config={
            "下单店铺": st.column_config.SelectboxColumn("下单店铺", options=SHOPS),
            "买家下单价(营收)": st.column_config.NumberColumn("买家下单价(营收)", min_value=0.0, format="%.2f")
        },
        use_container_width=True
    )
    
    if st.button("⚡ 一键拆分并保存所有订单", type="primary"):
        valid_rows = edited_batch[(edited_batch["书名"].str.strip() != "")]
        
        if len(valid_rows) == 0:
            st.error("❌ 请至少填写一行有效的书名！")
        elif batch_total_cost <= 0:
            st.error("❌ 请填写这批拼单的总花费！")
        else:
            per_item_cost = batch_total_cost / len(valid_rows)
            
            for index, row in valid_rows.iterrows():
                supabase.table("orders").insert({
                    "buyer_name": row["买家账号"],
                    "buyer_address": row["买家地址"],
                    "pickup_area": row["取件码地区"],
                    "book_name": row["书名"],
                    "shop_name": row["下单店铺"],
                    "status": "我方已下单",
                    "price_sell": row["买家下单价(营收)"],
                    "price_buy": per_item_cost,
                    "book_image": row["图片备注"],
                    "purchase_type": "合并拼单"
                }).execute()
                
            st.success(f"✅ 成功录入了 {len(valid_rows)} 笔拼单！已自动平摊成本。")
            st.rerun()

# ====== TAB 3: 自动发货汇总 (按买家合并书籍与地址) ======
with tab3:
    st.subheader("📦 待发货包裹自动汇总")
    st.info("💡 系统会自动将**同一个买家**分散的书籍、地址和取件码地区合并归类，方便一键打包发货！")
    
    if not df.empty:
        shipping_df = df[df["buyer_name"] != "暂无"].copy()
        
        if not shipping_df.empty:
            # 自动聚合汇总，加入 pickup_area
            summary_df = shipping_df.groupby(["buyer_name", "buyer_address", "pickup_area", "shop_name", "status"]).agg({
                "book_name": lambda x: " + ".join(x), 
                "book_image": lambda x: " | ".join([str(i) for i in x if i]), 
                "price_sell": "sum",
                "xianyu_no": "first"
            }).reset_index()
            
            summary_df = summary_df.rename(columns={
                "buyer_name": "买家账号",
                "buyer_address": "收货地址",
                "pickup_area": "取件码地区",
                "shop_name": "下单店铺",
                "status": "当前状态",
                "book_name": "合拼书单",
                "book_image": "版本/图片说明",
                "price_sell": "总营收",
                "xianyu_no": "闲鱼单号"
            })
            
            st.dataframe(summary_df, use_container_width=True, hide_index=True)
        else:
            st.warning("当前没有检测到有具名买家的订单。")
    else:
        st.info("暂无数据。")

# ---------------- 核心功能 4：订单看板 (状态修改) ----------------
st.divider()
st.subheader("📋 云端实时管理看板 (全部明细)")

if not df.empty:
    df = df.sort_values(by="order_time", ascending=False)
    
    st.markdown("##### 快速更新状态")
    edit_col1, edit_col2, edit_col3 = st.columns([2, 2, 1])
    with edit_col1:
        order_list = df["id"].astype(str) + " - " + df["buyer_name"] + " (" + df["book_name"] + ")"
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
        "pickup_area": "取件码地区",
        "xianyu_no": "闲鱼单号",
        "book_name": "书名",
        "shop_name": "店铺",
        "status": "状态",
        "price_sell": "订单营收",
        "price_buy": "订单成本",
        "book_image": "书本图片/说明",
        "order_time": "下单时间"
    })
    
    cols_to_show = ["编号", "进货方式", "买家账号", "买家地址", "取件码地区", "闲鱼单号", "书名", "书本图片/说明", "店铺", "status" if "status" in display_df.columns else "状态", "订单营收", "订单成本", "下单时间"]
    # 动态过滤确保列名正确
    available_cols = [c for c in ["编号", "进货方式", "买家账号", "买家地址", "取件码地区", "闲鱼单号", "书名", "书本图片/说明", "店铺", "状态", "订单营收", "订单成本", "下单时间"] if c in display_df.columns]
    
    st.dataframe(display_df[available_cols], use_container_width=True, hide_index=True)
else:
    st.info("目前云端数据库还没有订单哦！")
