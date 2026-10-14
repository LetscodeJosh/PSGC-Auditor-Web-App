import io
import time
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="ERPN PSGC Auditor",
    page_icon="✅",
    layout="wide",
)

# Minimalist UI styling
st.markdown(
    """
    <style>
        .block-container { padding-top: 1.5rem; padding-bottom: 1.5rem; }
        div[data-testid="stMetric"] {
            background-color: #f8f9fa;
            border: 1px solid #e9ecef;
            padding: 12px 16px;
            border-radius: 8px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_erpn_psgc_tree(base_url: str, username: str, password: str) -> pd.DataFrame:
    """Authenticates with ERPNext and fetches the full PSGC Location tree."""
    base_url = base_url.rstrip("/")
    session = requests.Session()

    # 1. Authenticate with ERPNext session login
    login_resp = session.post(
        f"{base_url}/api/method/login",
        data={"usr": username, "pwd": password},
        timeout=20,
    )
    login_resp.raise_for_status()

    # 2. Paginate through all PSGC Location records (Regions, Provinces, Cities)
    all_rows = []
    limit_start = 0
    page_size = 2500

    while True:
        params = {
            "fields": '["name","location_name","parent_psgc_location"]',
            "limit_start": limit_start,
            "limit_page_length": page_size,
        }
        resp = session.get(
            f"{base_url}/api/resource/PSGC Location",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("data", [])
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        limit_start += page_size

    df = pd.DataFrame(all_rows)
    if df.empty:
        return pd.DataFrame(columns=["name", "location_name", "parent_psgc_location"])

    df["name"] = df["name"].astype(str).str.strip()
    df["location_name"] = df["location_name"].fillna("").astype(str).str.strip()
    df["parent_psgc_location"] = (
        df["parent_psgc_location"].fillna("").astype(str).str.strip()
    )
    return df


def build_tree_from_fallback_sheet(excel_file) -> pd.DataFrame:
    """Builds the PSGC lookup table from the PSGC_Clean sheet if offline."""
    xls = pd.ExcelFile(excel_file)
    if "PSGC_Clean" not in xls.sheet_names:
        return pd.DataFrame()
    psgc_clean = pd.read_excel(excel_file, sheet_name="PSGC_Clean", dtype=str)
    records = {}
    for _, r in psgc_clean.iterrows():
        reg_id = str(r["Region_ID"]).strip()
        reg_name = str(r["Region_Name"]).strip()
        prov_id = str(r["Province_ID"]).strip()
        prov_name = str(r["Province_Name"]).strip()
        city_id = str(r["City_ID"]).strip()
        city_name = str(r["City_Name"]).strip()

        records[reg_id] = (reg_id, reg_name, "")
        records[prov_id] = (prov_id, prov_name, reg_id)
        records[city_id] = (city_id, city_name, prov_id)

    return pd.DataFrame(
        list(records.values()),
        columns=["name", "location_name", "parent_psgc_location"],
    )


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
    erpn_url = st.text_input(
        "ERPNext Base URL",
        value="https://dev.pmii-marketing.com",
    )
    erpn_user = st.text_input(
        "Username",
        value="jptan@profinsights.biz",
    )
    erpn_pass = st.text_input(
        "Password",
        value="pims@admin",
        type="password",
    )
    sync_btn = st.button("🔄 Sync PSGC Doctype", use_container_width=True)
    if sync_btn:
        st.cache_data.clear()

# --- HEADER ---
st.title("🏥 ERPNext PSGC Location Auditor")
st.caption("Minimalist Hierarchy Validator • `Region` → `Province` → `City/Municipality`")

# --- UPLOAD SECTION ---
uploaded_file = st.file_uploader(
    "Upload Institution Excel File (`.xlsx`)",
    type=["xlsx"],
)

if uploaded_file is not None:
    # 1. Load PSGC Tree from live ERPNext (with fallback if server blocks external requests)
    psgc_df = pd.DataFrame()
    source_label = "Live ERPNext API"
    try:
        with st.spinner("Connecting to ERPNext PSGC Location doctype..."):
            psgc_df = fetch_erpn_psgc_tree(erpn_url, erpn_user, erpn_pass)
    except Exception as exc:
        psgc_df = build_tree_from_fallback_sheet(uploaded_file)
        source_label = "Embedded PSGC_Clean Sheet (Offline Fallback)"
        if psgc_df.empty:
            st.error(f"Could not connect to ERPNext API: {exc}")
            st.stop()

    id_to_name = dict(zip(psgc_df["name"], psgc_df["location_name"]))
    id_to_parent = dict(zip(psgc_df["name"], psgc_df["parent_psgc_location"]))

    # 2. Read Uploaded Institution Sheet (preserving leading zeros as string)
    df_in = pd.read_excel(uploaded_file, sheet_name=0, dtype=str).fillna("")
    required_cols = ["Institution Name", "Region", "Province", "City/Municipality"]
    missing_cols = [c for c in required_cols if c not in df_in.columns]

    if missing_cols:
        st.error(f"Missing required columns: {', '.join(missing_cols)}")
        st.stop()

    # 3. Progress Bar Matching Analyzer
    progress_bar = st.progress(0, text="Starting PSGC hierarchy analysis...")
    total_rows = len(df_in)
    audit_rows = []
    batch_size = max(1, total_rows // 25)

    for idx, row in df_in.iterrows():
        inst = str(row["Institution Name"]).strip()
        reg = str(row["Region"]).strip()
        prov = str(row["Province"]).strip()
        city = str(row["City/Municipality"]).strip()

        issues = []

        # Format check (10-digit code or 11-char NCR city code)
        if len(reg) != 10:
            issues.append("Region length != 10")
        if len(prov) != 10:
            issues.append("Province length != 10")
        if len(city) not in (10, 11):
            issues.append("City length invalid")

        # Existence & Parent-Child Tree Check
        if reg not in id_to_parent:
            issues.append("Region not in PSGC")
        elif id_to_parent.get(reg, "") != "":
            issues.append("Region is not a Root node")

        if prov not in id_to_parent:
            issues.append("Province not in PSGC")
        elif id_to_parent.get(prov, "") != reg:
            issues.append(f"Province parent != {reg}")

        if city not in id_to_parent:
            issues.append("City not in PSGC")
        elif id_to_parent.get(city, "") != prov:
            issues.append(f"City parent != {prov}")

        is_valid = len(issues) == 0
        audit_rows.append(
            {
                "Status": "✅ Match" if is_valid else "❌ Mismatch",
                "Institution Name": inst,
                "Region": reg,
                "ERPN Region": id_to_name.get(reg, "NOT FOUND"),
                "Province": prov,
                "ERPN Province": id_to_name.get(prov, "NOT FOUND"),
                "City/Municipality": city,
                "ERPN City": id_to_name.get(city, "NOT FOUND"),
                "Audit Detail": "100% Valid Hierarchy" if is_valid else " | ".join(issues),
                "_valid": is_valid,
            }
        )

        if (idx + 1) % batch_size == 0 or (idx + 1) == total_rows:
            pct = int(((idx + 1) / total_rows) * 100)
            progress_bar.progress(
                pct,
                text=f"Analyzing PSGC hierarchy... {idx + 1:,} / {total_rows:,} rows ({pct}%)",
            )
            time.sleep(0.01)

    progress_bar.progress(100, text=f"Analysis complete ({source_label})")

    audit_df = pd.DataFrame(audit_rows)
    valid_count = int(audit_df["_valid"].sum())
    invalid_count = total_rows - valid_count
    accuracy_pct = (valid_count / total_rows * 100) if total_rows > 0 else 0.0

    # --- KPI METRICS ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Institutions", f"{total_rows:,}")
    c2.metric("100% Matched", f"{valid_count:,}")
    c3.metric("Mismatches", f"{invalid_count:,}")
    c4.metric("PSGC Accuracy", f"{accuracy_pct:.2f}%")

    # --- RESULTS TABS & EXPORT ---
    tab_all, tab_err, tab_export = st.tabs(
        [
            f"📋 All Records ({total_rows:,})",
            f"⚠️ Mismatches ({invalid_count:,})",
            "⬇️ Export ERPN Import File",
        ]
    )

    display_cols = [c for c in audit_df.columns if c != "_valid"]

    with tab_all:
        st.dataframe(audit_df[display_cols], use_container_width=True, height=460)

    with tab_err:
        if invalid_count == 0:
            st.success("All rows passed 100% PSGC Region → Province → City hierarchy validation.")
        else:
            st.dataframe(
                audit_df[~audit_df["_valid"]][display_cols],
                use_container_width=True,
                height=460,
            )

    with tab_export:
        clean_export_df = audit_df[
            ["Institution Name", "Region", "Province", "City/Municipality"]
        ]
        excel_bytes = to_clean_excel_bytes(clean_export_df)
        st.download_button(
            label="📥 Download Verified ERPN Data Import (.xlsx)",
            data=excel_bytes,
            file_name="ERPN_Verified_Institution_Import.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
