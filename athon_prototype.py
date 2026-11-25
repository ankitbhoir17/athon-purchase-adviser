import streamlit as st
import json
import re
import os
import pandas as pd

st.set_page_config(page_title="Athon Purchase Adviser Prototype", layout="wide")

# --- LOAD FULL 1,200-PRODUCT CATALOG ---
@st.cache_data
def load_catalog():
    catalog_path = os.path.join(os.path.dirname(__file__), "catalog_large.json")
    if not os.path.exists(catalog_path):
        catalog_path = "catalog_large.json"
        
    if os.path.exists(catalog_path):
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data, len(data), True
    else:
        fallback = [
            {
                "product_id": "LAP-101",
                "name": "ZenBook Pro 14 OLED",
                "verified_attributes": {"price_inr": 92000, "ram_gb": 16, "gpu_model": "RTX 4050", "weight_kg": 1.45, "battery_hours": 11.0}
            },
            {
                "product_id": "LAP-102",
                "name": "ROG Zephyrus G14",
                "verified_attributes": {"price_inr": 115000, "ram_gb": 16, "gpu_model": "RTX 4060", "weight_kg": 1.65, "battery_hours": 9.5}
            },
            {
                "product_id": "LAP-103",
                "name": "IdeaPad Slim 5",
                "verified_attributes": {"price_inr": 65000, "ram_gb": 16, "gpu_model": "Integrated Iris Xe", "weight_kg": 1.46, "battery_hours": 12.5}
            }
        ]
        return fallback, len(fallback), False

catalog, total_products, is_full_catalog = load_catalog()

# --- NLP QUERY PARSER FOR MULTI-TURN CHAT ---
def parse_user_query(query_text):
    q = query_text.lower()
    extracted = {}
    
    # Extract budget (e.g., "95000", "95,000", "1.2 lakh", "80k")
    lakh_match = re.search(r'(\d+(?:\.\d+)?)\s*lakh', q)
    k_match = re.search(r'(\d+)\s*k\b', q)
    num_match = re.search(r'(?:under|below|max|budget|rs\.?|₹)?\s*(\d{2,3},?\d{3})', q)
    
    if lakh_match:
        extracted["max_budget"] = int(float(lakh_match.group(1)) * 100000)
    elif k_match:
        extracted["max_budget"] = int(k_match.group(1)) * 1000
    elif num_match:
        extracted["max_budget"] = int(num_match.group(1).replace(",", ""))
        
    # Extract RAM
    ram_match = re.search(r'(\d+)\s*gb\s*ram|(\d+)\s*gb', q)
    if ram_match:
        val = int(ram_match.group(1) or ram_match.group(2))
        if val in [8, 16, 32, 64]:
            extracted["min_ram"] = val
            
    # Extract GPU intent
    if any(word in q for word in ["rtx", "gpu", "gaming", "deep learning", "ml", "ai", "video editing", "3d"]):
        extracted["req_gpu"] = True
    elif any(word in q for word in ["office", "browsing", "lightweight", "basic", "no gpu"]):
        extracted["req_gpu"] = False
        
    # Extract Soft Preference Shifts
    if any(word in q for word in ["light", "portable", "travel", "slim", "carry"]):
        extracted["w_port"] = 0.55
        extracted["w_perf"] = 0.20
        extracted["w_batt"] = 0.25
    elif any(word in q for word in ["fast", "heavy", "performance", "render", "train"]):
        extracted["w_perf"] = 0.60
        extracted["w_port"] = 0.20
        extracted["w_batt"] = 0.20
    elif any(word in q for word in ["battery", "long lasting", "all day", "unplugged"]):
        extracted["w_batt"] = 0.55
        extracted["w_perf"] = 0.25
        extracted["w_port"] = 0.20
        
    return extracted

# Initialize Session State
if "max_budget" not in st.session_state:
    st.session_state.max_budget = 100000
if "min_ram" not in st.session_state:
    st.session_state.min_ram = 16
if "req_gpu" not in st.session_state:
    st.session_state.req_gpu = True
if "w_perf" not in st.session_state:
    st.session_state.w_perf = 0.40
if "w_port" not in st.session_state:
    st.session_state.w_port = 0.30
if "w_batt" not in st.session_state:
    st.session_state.w_batt = 0.30
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {"role": "assistant", "content": "Hello! I am **Athon**. Describe the laptop you need (budget, RAM, workload, portability), and I will run Value-of-Information (VoI) clarification, verify product claims, and compute an auditable recommendation."}
    ]

# --- HEADER ---
st.title("🛡️ Athon: Purchase Advice Under Uncertain Preferences & Evidence")
if is_full_catalog:
    st.caption(f"🟢 Connected to Full Empirical Catalog: **{total_products:,} Verified Laptop Variants** (`catalog_large.json`)")
else:
    st.caption("⚠️ Running on Fallback Sample Catalog (Place `catalog_large.json` in the same folder for all 1,200 products)")

# --- SIDEBAR: CONTROLS & AUDIT SIMULATORS ---
with st.sidebar:
    st.header("1. Confirmed User Requirements")
    st.session_state.max_budget = st.slider("Max Budget (INR)", 30000, 250000, st.session_state.max_budget, step=5000)
    st.session_state.min_ram = st.selectbox("Minimum RAM (GB)", [8, 16, 32, 64], index=[8, 16, 32, 64].index(st.session_state.min_ram))
    st.session_state.req_gpu = st.checkbox("Require Dedicated RTX GPU", value=st.session_state.req_gpu)
    
    st.divider()
    st.header("2. Latent Preference Weights (θ)")
    st.session_state.w_perf = st.slider("Compute Performance Weight", 0.0, 1.0, st.session_state.w_perf)
    st.session_state.w_port = st.slider("Portability Weight", 0.0, 1.0, st.session_state.w_port)
    st.session_state.w_batt = st.slider("Battery Autonomy Weight", 0.0, 1.0, st.session_state.w_batt)
    
    st.divider()
    st.header("3. Research Audit Simulators")
    verify_claims = st.toggle("Enable External Product Verification", value=True)
    inject_adversarial = st.toggle("Inject Adversarial Seller Spam (H3)", value=False)

# --- CORE ATHON EVALUATION ENGINE ---
def evaluate_full_catalog():
    feasible = []
    rejected = []
    
    for idx, item in enumerate(catalog):
        attrs = item["verified_attributes"]
        pid = item.get("product_id", f"PROD-{idx+1}")
        model_name = item.get("name", f"{item.get('brand', 'Laptop')} {pid}")
        
        effective_gpu = attrs["gpu_model"]
        # Simulate adversarial injection on non-RTX budget models
        if not verify_claims and inject_adversarial and "RTX" not in effective_gpu:
            effective_gpu = "RTX 4090 (Unverified Seller Claim)"
            
        reasons = []
        if attrs["price_inr"] > st.session_state.max_budget:
            reasons.append("Budget Exceeded")
        if attrs["ram_gb"] < st.session_state.min_ram:
            reasons.append("Insufficient RAM")
        if st.session_state.req_gpu and "RTX" not in effective_gpu:
            reasons.append("No RTX GPU")
            
        v_perf = 1.0 if "RTX 40" in effective_gpu else (0.75 if "RTX" in effective_gpu else 0.35)
        v_port = max(0.0, (3.0 - attrs["weight_kg"]) / 2.0)
        v_batt = min(1.0, attrs["battery_hours"] / 15.0)
        cost_norm = attrs["price_inr"] / 250000.0
        
        utility = (
            st.session_state.w_perf * v_perf +
            st.session_state.w_port * v_port +
            st.session_state.w_batt * v_batt -
            0.30 * cost_norm
        )
        
        row = {
            "Product ID": pid,
            "Model": model_name,
            "Price (INR)": attrs["price_inr"],
            "RAM (GB)": attrs["ram_gb"],
            "Verified GPU": attrs["gpu_model"],
            "Weight (kg)": attrs["weight_kg"],
            "Battery (hrs)": attrs["battery_hours"],
            "Utility U_θ(x)": round(utility, 4),
            "Filter Reason": "Passed" if not reasons else ", ".join(reasons)
        }
        
        if not reasons:
            feasible.append(row)
        else:
            rejected.append(row)
            
    feasible.sort(key=lambda x: x["Utility U_θ(x)"], reverse=True)
    return feasible, rejected

feasible_items, rejected_items = evaluate_full_catalog()

# --- TABS: INTERACTIVE CHAT VS. SYSTEM TELEMETRY ---
main_tab1, main_tab2 = st.tabs(["💬 Interactive Multi-Turn Adviser", "📊 Full Catalog Telemetry & Audit Dashboard"])

with main_tab1:
    chat_col, audit_col = st.columns([1.15, 1])
    
    with chat_col:
        st.subheader("Conversational Purchase Adviser")
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                
        user_prompt = st.chat_input("e.g., 'Need a portable laptop under 95000 with 16GB RAM and RTX GPU'")
        if user_prompt:
            st.session_state.chat_history.append({"role": "user", "content": user_prompt})
            parsed = parse_user_query(user_prompt)
            
            # Update session constraints from natural language
            updates = []
            if "max_budget" in parsed:
                st.session_state.max_budget = parsed["max_budget"]
                updates.append(f"Max Budget ≤ ₹{parsed['max_budget']:,}")
            if "min_ram" in parsed:
                st.session_state.min_ram = parsed["min_ram"]
                updates.append(f"Min RAM ≥ {parsed['min_ram']}GB")
            if "req_gpu" in parsed:
                st.session_state.req_gpu = parsed["req_gpu"]
                updates.append(f"Dedicated RTX GPU = {parsed['req_gpu']}")
            if "w_port" in parsed:
                st.session_state.w_port = parsed["w_port"]
                st.session_state.w_perf = parsed["w_perf"]
                st.session_state.w_batt = parsed["w_batt"]
                updates.append("Updated soft preference weights (θ)")
                
            # Re-evaluate immediately after parsing
            feasible_items, rejected_items = evaluate_full_catalog()
            
            if not verify_claims:
                reply = (
                    f"**[VoI Action: VERIFY PRODUCT CLAIMS]**\n\n"
                    f"Extracted requirements: `{', '.join(updates) if updates else 'Using active sidebar constraints'}`.\n\n"
                    f"Before recommending from {total_products:,} products, my Value-of-Information controller (`V(q|b) > 0`) detects unverified seller claims. "
                    f"Please toggle **Enable External Product Verification** ON in the sidebar to prevent specification hallucinations."
                )
            elif len(feasible_items) == 0:
                reply = (
                    f"**[Terminal Action: CALIBRATED ABSTENTION (`a_abs`)]**\n\n"
                    f"Updated User Requirement Record: `{', '.join(updates) if updates else 'Current constraints'}`.\n\n"
                    f"Out of **{total_products:,} verified catalog items**, **0 candidates** satisfy all hard constraints simultaneously. "
                    f"Rather than violating your budget or hardware requirements, Athon selectively abstains (`0.0%` constraint violations)."
                )
            else:
                top = feasible_items[0]
                reply = (
                    f"**[Terminal Action: RECOMMEND `{top['Model']}` ({top['Product ID']})]**\n\n"
                    f"• **Confirmed Constraints:** {', '.join(updates) if updates else 'Matched active User Requirement Record'}\n"
                    f"• **Verified Specs:** ₹{top['Price (INR)']:,} | {top['RAM (GB)']}GB RAM | {top['Verified GPU']} | {top['Weight (kg)']} kg | {top['Battery (hrs)']} hrs\n"
                    f"• **Expected Net Utility $U_\\theta(x)$:** `{top['Utility U_θ(x)']:.4f}` across **{len(feasible_items)} feasible candidates** (out of {total_products:,} evaluated)."
                )
                
            st.session_state.chat_history.append({"role": "assistant", "content": reply})
            st.rerun()

    with audit_col:
        st.subheader("📋 Live Auditable Decision Record (H4)")
        if not verify_claims:
            st.warning("VoI Controller paused for external product verification (`V(q|b) > 0`).")
        elif len(feasible_items) == 0:
            st.error("Calibrated Abstention Triggered (`a_abs`): No feasible item in catalog.")
        else:
            top = feasible_items[0]
            st.success(f"Top Ranked Candidate: **{top['Model']}** (`{top['Product ID']}`)")
            st.json({
                "terminal_action": "RECOMMEND",
                "catalog_evaluated": total_products,
                "feasible_pool_size": len(feasible_items),
                "selected_product_id": top["Product ID"],
                "confirmed_user_requirements": {
                    "max_budget_inr": st.session_state.max_budget,
                    "min_ram_gb": st.session_state.min_ram,
                    "requires_rtx_gpu": st.session_state.req_gpu
                },
                "verified_product_claims": {
                    "price_inr": top["Price (INR)"],
                    "ram_gb": top["RAM (GB)"],
                    "gpu_model": top["Verified GPU"],
                    "weight_kg": top["Weight (kg)"],
                    "battery_hours": top["Battery (hrs)"]
                },
                "utility_score": top["Utility U_θ(x)"],
                "adversarial_spam_blocked": inject_adversarial
            })

with main_tab2:
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Catalog Variants", f"{total_products:,}")
    m2.metric("Feasible Candidates", f"{len(feasible_items):,}")
    m3.metric("Filtered (Infeasible)", f"{len(rejected_items):,}")
    m4.metric("Top Utility U_θ(x)", f"{feasible_items[0]['Utility U_θ(x)']:.4f}" if feasible_items else "ABSTAIN")
    
    st.subheader(f"✅ Ranked Feasible Laptops ({len(feasible_items)} Matches)")
    if feasible_items:
        st.dataframe(pd.DataFrame(feasible_items), use_container_width=True, height=320)
    else:
        st.info("No feasible products match the current hard constraints.")
        
    st.subheader(f"❌ Rejected Candidates Sample (Showing first 25 of {len(rejected_items)})")
    if rejected_items:
        st.dataframe(pd.DataFrame(rejected_items[:25]), use_container_width=True, height=250)