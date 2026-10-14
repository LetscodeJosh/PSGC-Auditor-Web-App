import io
import time
from urllib.parse import quote
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="ERPN PSGC 1-by-1 Auditor",
    page_icon="✅",
    layout="wide",
)

# Theme-adaptive CSS: fixes invisible white-on-white text in Dark Mode
st.markdown(
    """
    <style>
        .block-container { padding-top: 1.2rem; padding-bottom: 1.5rem; }
        div[data-testid="stMetric"] {
            background-color: rgba(128, 128, 128, 0.12) !important;
            border: 1px solid rgba(128, 128, 128, 0.28) !important;
            padding: 14px 18px !important;
            border-radius: 10px !important;
        }
        div[data-testid="stMetricLabel"] p,
        div[data-testid="stMetricValue"] div {
            color: inherit !important;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_erpn_session(base_url: str, username: str, password: str):
    """Authenticates with ERPNext and returns a persistent session."""
    base_url = base_url.rstrip("/")
    session = requests.Session()
    resp = session.post(
        f"{base_url}/api/method/login",
        data={"usr": username, "pwd": password},
        timeout=15,
    )
    resp.raise_for_status()
    return session


def fetch_single_psgc_doc(
    session: requests.Session, base_url: str, code: str
) -> dict | None:
    """Fetches a single PSGC Location document directly from ERPNext by ID."""
    if not code:
        return None
    base_url = base_url.rstrip("/")
    url = f"{base_url}/api/resource/PSGC Location/{quote(str(code).strip())}"
    resp = session.get(url, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if not data:
        return None

    loc_name = (
        data.get("location_name")
        or data.get("psgc_location_name")
        or data.get("title")
        or data.get("name")
        or ""
    )
    parent = (
        data.get("parent_psgc_location")
        or data.get("parent")
        or ""
    )
    return {
        "name": str(data.get("name", code)).strip(),
        "location_name": str(loc_name).strip(),
        "parent_psgc_location": str(parent).strip() if parent else "",
    }


@st.cache_data(show_spinner=False)
def load_fallback_tree(file_bytes: bytes) -> tuple[dict, dict]:
    """Loads PSGC_Clean sheet from uploaded workbook if available."""
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    if "PSGC_Clean" not in xls.sheet_names:
        return {}, {}
    psgc_clean = pd.read_excel(
        io.BytesIO(file_bytes), sheet_name="PSGC_Clean", dtype=str
    ).fillna("")
    id_to_name = {}
    id_to_parent = {}
    for _, r in psgc_clean.iterrows():
        reg_id = str(r["Region_ID"]).strip()
        reg_name = str(r["Region_Name"]).strip()
        prov_id = str(r["Province_ID"]).strip()
        prov_name = str(r["Province_Name"]).strip()
        city_id = str(r["City_ID"]).strip()
        city_name = str(r["City_Name"]).strip()

        id_to_name[reg_id] = reg_name
        id_to_parent[reg_id] = ""
        id_to_name[prov_id] = prov_name
        id_to_parent[prov_id] = reg_id
        id_to_name[city_id] = city_name
        id_to_parent[city_id] = prov_id

    return id_to_name, id_to_parent


def audit_single_triplet(
    reg_code: str,
    prov_code: str,
    city_code: str,
    base_url: str,
    username: str,
    password: str,
    fallback_names: dict,
    fallback_parents: dict,
    progress_bar=None,
) -> dict:
    """Audits a single (Region, Province, City/Municipality) 1-by-1 with progress bar."""
    reg_code = str(reg_code).strip()
    prov_code = str(prov_code).strip()
    city_code = str(city_code).strip()

    live_mode = False
    session = None
    try:
        session = get_erpn_session(base_url, username, password)
        live_mode = True
    except Exception:
        live_mode = False

    def resolve_node(code: str) -> dict | None:
        if live_mode and session is not None:
            try:
                doc = fetch_single_psgc_doc(session, base_url, code)
                if doc is not None:
                    return doc
            except Exception:
                pass
        if code in fallback_parents:
            return {
                "name": code,
                "location_name": fallback_names.get(code, ""),
                "parent_psgc_location": fallback_parents.get(code, ""),
            }
        return None

    # Step 1: Verify Region
    if progress_bar:
        progress_bar.progress(30, text=f"Step 1/3: Checking Region ({reg_code})...")
        time.sleep(0.06)
    reg_doc = resolve_node(reg_code)
    reg_ok = (
        reg_doc is not None
        and len(reg_code) == 10
        and reg_doc["parent_psgc_location"] == ""
    )
    reg_msg = (
        "Valid Root Region"
        if reg_ok
        else (
            "Not found in PSGC"
            if not reg_doc
            else f"Invalid parent ({reg_doc['parent_psgc_location']})"
        )
    )

    # Step 2: Verify Province
    if progress_bar:
        progress_bar.progress(65, text=f"Step 2/3: Checking Province ({prov_code})...")
        time.sleep(0.06)
    prov_doc = resolve_node(prov_code)
    prov_ok = (
        prov_doc is not None
        and len(prov_code) == 10
        and prov_doc["parent_psgc_location"] == reg_code
    )
    prov_msg = (
        f"Linked to Region {reg_code}"
        if prov_ok
        else (
            "Not found in PSGC"
            if not prov_doc
            else f"Parent is {prov_doc['parent_psgc_location']} (Expected {reg_code})"
        )
    )

    # Step 3: Verify City/Municipality
    if progress_bar:
        progress_bar.progress(
            100, text=f"Step 3/3: Checking City/Municipality ({city_code})..."
        )
        time.sleep(0.06)
    city_doc = resolve_node(city_code)
    city_ok = (
        city_doc is not None
        and len(city_code) in (10, 11)
        and city_doc["parent_psgc_location"] == prov_code
    )
    city_msg = (
        f"Linked to Province {prov_code}"
        if city_ok
        else (
            "Not found in PSGC"
            if not city_doc
            else f"Parent is {city_doc['parent_psgc_location']} (Expected {prov_code})"
        )
    )

    return {
        "all_ok": reg_ok and prov_ok and city_ok,
        "source": "Live ERPNext Doctype" if live_mode else "Embedded PSGC Reference",
        "reg_ok": reg_ok,
        "reg_name": reg_doc["location_name"] if reg_doc else "NOT FOUND",
        "reg_msg": reg_msg,
        "prov_ok": prov_ok,
        "prov_name": prov_doc["location_name"] if prov_doc else "NOT FOUND",
        "prov_msg": prov_msg,
        "city_ok": city_ok,
        "city_name": city_doc["location_name"] if city_doc else "NOT FOUND",
        "city_msg": city_msg,
    }


def to_clean_excel_bytes(df_export: pd.DataFrame) -> bytes:
    """Exports validated DataFrame to Excel with strict Text (@) formatting."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_export.to_excel(writer, sheet_name="Institution", index=False)
        ws = writer.sheets["Institution"]
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=2, max_col=4):
            for cell in row:
                cell.number_format = "@"
    return output.getvalue()


# --- SIDEBAR: ERPNext Connection ---
with st.sidebar:
    st.subheader("🔗 ERPNext Connection")
    erpn_url = st.text_input("ERPNext Base URL", value="https://dev.pmii-marketing.com")
    erpn_user = st.text_input("Username", value="jptan@profinsights.biz")
    erpn_pass = st.text_input("Password", value="pims@admin", type="password")
    if st.button("🔄 Reset Connection Cache", use_container_width=True):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.toast("Connection cache cleared.")

# --- HEADER ---
st.title("🏥 ERPNext PSGC 1-by-1 Auditor")
st.caption("Step-by-Step Hierarchy Validator • `Region` → `Province` → `City/Municipality`")

tab_step, tab_manual = st.tabs(["🔍 1-by-1 File Auditor", "🧪 Single Code Tester"])

with tab_step:
    uploaded_file = st.file_uploader(
        "Upload Institution Excel File (`.xlsx`)", type=["xlsx"]
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        fallback_names, fallback_parents = load_fallback_tree(file_bytes)

        df_in = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, dtype=str).fillna("")
        required_cols = ["Institution Name", "Region", "Province", "City/Municipality"]
        missing_cols = [c for c in required_cols if c not in df_in.columns]

        if missing_cols:
            st.error(f"Missing required columns: {', '.join(missing_cols)}")
            st.stop()

        # Optional DOH source columns if Audit_Verification exists
        xls_sheets = pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names
        audit_source_df = None
        if "Audit_Verification" in xls_sheets:
            audit_source_df = pd.read_excel(
                io.BytesIO(file_bytes), sheet_name="Audit_Verification", dtype=str
            ).fillna("")

        total_rows = len(df_in)

        # Initialize session state for 1-by-1 navigation and verified tracking
        if (
            "current_idx" not in st.session_state
            or st.session_state.get("file_name") != uploaded_file.name
        ):
            st.session_state.current_idx = 0
            st.session_state.verified_rows = set()
            st.session_state.file_name = uploaded_file.name

        # Top KPI Metrics (Dark-Mode & Light-Mode Safe)
        idx = st.session_state.current_idx
        verified_count = len(st.session_state.verified_rows)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Current Record", f"{idx + 1:,} / {total_rows:,}")
        m2.metric("Manually Verified", f"{verified_count:,}")
        m3.metric("Remaining", f"{total_rows - verified_count:,}")
        m4.metric("Review Progress", f"{(verified_count / total_rows * 100):.1f}%")

        # 1-by-1 Navigation Controls
        nav1, nav2, nav3, nav4 = st.columns([1.2, 5.2, 1.2, 1.8])

        with nav1:
            if st.button("⬅️ Previous", use_container_width=True, disabled=(idx <= 0)):
                st.session_state.current_idx = max(0, idx - 1)
                st.rerun()

        with nav2:
            options = [
                f"[Row {i + 1}] {'✅ ' if i in st.session_state.verified_rows else ''}{df_in.iloc[i]['Institution Name']}"
                for i in range(total_rows)
            ]
            selected_option = st.selectbox(
                "Select or Search Institution to Audit (1-by-1)",
                options=options,
                index=idx,
                label_visibility="collapsed",
            )
            new_idx = options.index(selected_option)
            if new_idx != idx:
                st.session_state.current_idx = new_idx
                st.rerun()

        with nav3:
            if st.button(
                "Next ➡️", use_container_width=True, disabled=(idx >= total_rows - 1)
            ):
                st.session_state.current_idx = min(total_rows - 1, idx + 1)
                st.rerun()

        with nav4:
            if st.button("✅ Verify & Next", type="primary", use_container_width=True):
                st.session_state.verified_rows.add(idx)
                if idx < total_rows - 1:
                    st.session_state.current_idx = idx + 1
                st.rerun()

        # Run 1-by-1 Audit on Current Record with Progress Bar
        cur_row = df_in.iloc[st.session_state.current_idx]
        inst_name = str(cur_row["Institution Name"]).strip()
        reg_code = str(cur_row["Region"]).strip()
        prov_code = str(cur_row["Province"]).strip()
        city_code = str(cur_row["City/Municipality"]).strip()

        pbar = st.progress(0, text="Initializing 1-by-1 PSGC hierarchy check...")
        res = audit_single_triplet(
            reg_code,
            prov_code,
            city_code,
            erpn_url,
            erpn_user,
            erpn_pass,
            fallback_names,
            fallback_parents,
            progress_bar=pbar,
        )
        pbar.progress(
            100,
            text=(
                f"1-by-1 Check Complete • Source: {res['source']} • "
                f"Record {st.session_state.current_idx + 1} of {total_rows}"
            ),
        )

        # Display 1-by-1 Audit Card using native theme-adaptive bordered containers
        with st.container(border=True):
            head_left, head_right = st.columns([3, 1])
            with head_left:
                st.subheader(f"🏥 {inst_name}")
            with head_right:
                if res["all_ok"]:
                    st.success("✅ 100% PSGC MATCH")
                else:
                    st.error("❌ HIERARCHY MISMATCH")

            c_reg, c_prov, c_city = st.columns(3)

            doh_reg, doh_prov, doh_city = "", "", ""
            if audit_source_df is not None and st.session_state.current_idx < len(
                audit_source_df
            ):
                arow = audit_source_df.iloc[st.session_state.current_idx]
                doh_reg = str(arow.get("DOH_Region", ""))
                doh_prov = str(arow.get("DOH_Province", ""))
                doh_city = str(arow.get("DOH_City", ""))

            with c_reg:
                with st.container(border=True):
                    st.caption("LEVEL 1 • REGION")
                    st.markdown(f"### `{reg_code}`")
                    st.write(f"**ERPNext Name:** {res['reg_name']}")
                    if doh_reg:
                        st.caption(f"DOH Original: {doh_reg}")
                    if res["reg_ok"]:
                        st.success(f"✔ {res['reg_msg']}")
                    else:
                        st.error(f"✖ {res['reg_msg']}")

            with c_prov:
                with st.container(border=True):
                    st.caption("LEVEL 2 • PROVINCE")
                    st.markdown(f"### `{prov_code}`")
                    st.write(f"**ERPNext Name:** {res['prov_name']}")
                    if doh_prov:
                        st.caption(f"DOH Original: {doh_prov}")
                    if res["prov_ok"]:
                        st.success(f"✔ {res['prov_msg']}")
                    else:
                        st.error(f"✖ {res['prov_msg']}")

            with c_city:
                with st.container(border=True):
                    st.caption("LEVEL 3 • CITY / MUNICIPALITY")
                    st.markdown(f"### `{city_code}`")
                    st.write(f"**ERPNext Name:** {res['city_name']}")
                    if doh_city:
                        st.caption(f"DOH Original: {doh_city}")
                    if res["city_ok"]:
                        st.success(f"✔ {res['city_msg']}")
                    else:
                        st.error(f"✖ {res['city_msg']}")

        # Download Clean Import File
        st.divider()
        dl_col1, dl_col2 = st.columns([3, 1])
        with dl_col1:
            st.caption(
                "Ready to import into ERPNext? Download the 4-column Institution file with Text-formatted PSGC codes."
            )
        with dl_col2:
            clean_export_df = df_in[
                ["Institution Name", "Region", "Province", "City/Municipality"]
            ]
            st.download_button(
                label="📥 Download ERPN Import (.xlsx)",
                data=to_clean_excel_bytes(clean_export_df),
                file_name="ERPN_Verified_Institution_Import.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )

with tab_manual:
    st.subheader("🧪 Check Any Single PSGC Combination (1-by-1)")
    col_a, col_b, col_c, col_btn = st.columns([2, 2, 2, 1.2])
    with col_a:
        m_reg = st.text_input("Region PSGC Code", value="0100000000")
    with col_b:
        m_prov = st.text_input("Province PSGC Code", value="0102800000")
    with col_c:
        m_city = st.text_input("City/Municipality PSGC Code", value="0102804000")
    with col_btn:
        st.write("")
        run_manual = st.button("🔍 Check Now", type="primary", use_container_width=True)

    if run_manual:
        m_pbar = st.progress(0, text="Checking single PSGC combination...")
        m_res = audit_single_triplet(
            m_reg,
            m_prov,
            m_city,
            erpn_url,
            erpn_user,
            erpn_pass,
            {},
            {},
            progress_bar=m_pbar,
        )
        if m_res["all_ok"]:
            st.success(
                f"✅ **100% Valid Hierarchy** ({m_res['source']}): "
                f"`{m_reg}` ({m_res['reg_name']}) → `{m_prov}` ({m_res['prov_name']}) → `{m_city}` ({m_res['city_name']})"
            )
        else:
            st.error(
                f"❌ **Hierarchy Mismatch**: Region: {m_res['reg_msg']} | "
                f"Province: {m_res['prov_msg']} | City: {m_res['city_msg']}"
            )
