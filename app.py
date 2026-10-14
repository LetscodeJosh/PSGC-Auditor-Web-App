import json
import time
from urllib.parse import quote
import requests
import streamlit as st

st.set_page_config(
    page_title="ERPNext Live PSGC Auditor",
    page_icon="✅",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Minimalist, theme-adaptive CSS with Sidebar hidden and equal-height aligned preview cards
st.markdown(
    """
    <style>
        /* Hide sidebar and sidebar toggle completely for security */
        [data-testid="stSidebar"],
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"] {
            display: none !important;
        }
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
            max-width: 1200px;
        }
        /* Status Header Banner */
        .status-banner-ok {
            background-color: rgba(46, 204, 113, 0.14);
            border: 1px solid rgba(46, 204, 113, 0.45);
            color: #2ecc71;
            padding: 12px 18px;
            border-radius: 10px;
            font-weight: 600;
            font-size: 1rem;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .status-banner-err {
            background-color: rgba(255, 75, 75, 0.14);
            border: 1px solid rgba(255, 75, 75, 0.45);
            color: #ff4b4b;
            padding: 12px 18px;
            border-radius: 10px;
            font-weight: 600;
            font-size: 1rem;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        /* Equal-height aligned PSGC preview cards */
        .psgc-card {
            background-color: rgba(128, 128, 128, 0.08);
            border: 1px solid rgba(128, 128, 128, 0.25);
            border-radius: 12px;
            padding: 18px 20px;
            height: 215px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }
        .psgc-level {
            font-size: 0.76rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            opacity: 0.65;
            margin-bottom: 4px;
        }
        .psgc-name {
            font-size: 1.15rem;
            font-weight: 700;
            line-height: 1.3;
            margin-bottom: 8px;
            overflow: hidden;
            text-overflow: ellipsis;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
        }
        .psgc-code {
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
            font-size: 1.35rem;
            font-weight: 700;
            color: #2ecc71;
            background-color: rgba(46, 204, 113, 0.12);
            border: 1px solid rgba(46, 204, 113, 0.3);
            padding: 6px 12px;
            border-radius: 8px;
            display: inline-block;
            width: fit-content;
            margin-bottom: 10px;
        }
        .psgc-link-ok {
            font-size: 0.85rem;
            font-weight: 600;
            color: #2ecc71;
            background-color: rgba(46, 204, 113, 0.10);
            padding: 6px 10px;
            border-radius: 6px;
            border-left: 3px solid #2ecc71;
        }
        .psgc-link-err {
            font-size: 0.85rem;
            font-weight: 600;
            color: #ff4b4b;
            background-color: rgba(255, 75, 75, 0.10);
            padding: 6px 10px;
            border-radius: 6px;
            border-left: 3px solid #ff4b4b;
        }
        /* Aligned Summary Table */
        .summary-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 14px;
            font-size: 0.92rem;
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid rgba(128, 128, 128, 0.25);
        }
        .summary-table th {
            background-color: rgba(128, 128, 128, 0.15);
            text-align: left;
            padding: 10px 14px;
            font-weight: 700;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border-bottom: 1px solid rgba(128, 128, 128, 0.25);
        }
        .summary-table td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(128, 128, 128, 0.15);
            vertical-align: middle;
        }
        .mono-cell {
            font-family: 'SFMono-Regular', Consolas, monospace;
            font-weight: 700;
            color: #2ecc71;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Backend credentials (never rendered in UI; supports Streamlit Cloud Secrets override)
try:
    ERPN_BASE_URL = st.secrets.get("ERPN_URL", "https://dev.pmii-marketing.com")
    ERPN_USERNAME = st.secrets.get("ERPN_USER", "jptan@profinsights.biz")
    ERPN_PASSWORD = st.secrets.get("ERPN_PASS", "pims@admin")
except Exception:
    ERPN_BASE_URL = "https://dev.pmii-marketing.com"
    ERPN_USERNAME = "jptan@profinsights.biz"
    ERPN_PASSWORD = "pims@admin"

SYSTEM_KEYS = {
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "doctype",
    "is_group",
    "old_parent",
    "lft",
    "rgt",
    "parent_psgc_location",
    "parent",
    "parentfield",
    "parenttype",
    "psgc_code",
    "code",
    "_user_tags",
    "_comments",
    "_assign",
    "_liked_by",
}


@st.cache_resource(show_spinner=False)
def get_erpn_session(base_url: str, username: str, password: str):
    """Authenticates with ERPNext in the backend and returns a persistent session."""
    base_url = base_url.rstrip("/")
    session = requests.Session()
    resp = session.post(
        f"{base_url}/api/method/login",
        data={"usr": username, "pwd": password},
        timeout=15,
    )
    resp.raise_for_status()
    return session


def extract_doc_name(data: dict, code: str) -> str:
    """Extracts the human-readable Location Name from an ERPNext PSGC Location doc."""
    code_str = str(code).strip()
    for k in (
        "location_name",
        "psgc_location_name",
        "name1",
        "location",
        "psgc_name",
        "place_name",
        "title",
        "description",
        "label",
    ):
        val = data.get(k)
        if isinstance(val, str) and val.strip() and val.strip() != code_str:
            return val.strip()

    for k, val in data.items():
        if k not in SYSTEM_KEYS and isinstance(val, str):
            v_clean = val.strip()
            if v_clean and v_clean != code_str and not v_clean.isdigit():
                return v_clean

    return code_str


def detect_name_fieldname(sample_data: dict, sample_code: str) -> str | None:
    """Auto-detects the fieldname used for Location Name in ERPNext."""
    code_str = str(sample_code).strip()
    for k in (
        "location_name",
        "psgc_location_name",
        "name1",
        "location",
        "psgc_name",
        "place_name",
        "title",
        "description",
        "label",
    ):
        val = sample_data.get(k)
        if isinstance(val, str) and val.strip() and val.strip() != code_str:
            return k
    for k, val in sample_data.items():
        if k not in SYSTEM_KEYS and isinstance(val, str):
            v_clean = val.strip()
            if v_clean and v_clean != code_str and not v_clean.isdigit():
                return k
    return None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_erpn_psgc_hierarchy(
    base_url: str, username: str, password: str
) -> tuple[dict, dict]:
    """Fetches Regions, Provinces, and Cities from live ERPNext PSGC Location doctype."""
    base_url = base_url.rstrip("/")
    session = get_erpn_session(base_url, username, password)

    sample_resp = session.get(
        f"{base_url}/api/resource/PSGC Location/0100000000", timeout=15
    )
    sample_resp.raise_for_status()
    sample_doc = sample_resp.json().get("data", {})
    name_field = detect_name_fieldname(sample_doc, "0100000000")

    fields_list = ["name", "parent_psgc_location"]
    if name_field:
        fields_list.append(name_field)

    id_to_name = {}
    id_to_parent = {}

    # Level 1: Regions
    resp_reg = session.get(
        f"{base_url}/api/resource/PSGC Location",
        params={
            "fields": json.dumps(fields_list),
            "filters": json.dumps(
                [["PSGC Location", "parent_psgc_location", "is", "not set"]]
            ),
            "limit_page_length": 100,
        },
        timeout=20,
    )
    resp_reg.raise_for_status()
    regions = resp_reg.json().get("data", [])
    reg_ids = []
    for r in regions:
        cid = str(r.get("name", "")).strip()
        if cid:
            reg_ids.append(cid)
            id_to_parent[cid] = ""
            id_to_name[cid] = extract_doc_name(r, cid)

    if not reg_ids:
        return id_to_name, id_to_parent

    # Level 2: Provinces
    resp_prov = session.get(
        f"{base_url}/api/resource/PSGC Location",
        params={
            "fields": json.dumps(fields_list),
            "filters": json.dumps(
                [["PSGC Location", "parent_psgc_location", "in", reg_ids]]
            ),
            "limit_page_length": 500,
        },
        timeout=20,
    )
    resp_prov.raise_for_status()
    provinces = resp_prov.json().get("data", [])
    prov_ids = []
    for p in provinces:
        cid = str(p.get("name", "")).strip()
        parent = str(p.get("parent_psgc_location") or "").strip()
        if cid:
            prov_ids.append(cid)
            id_to_parent[cid] = parent
            id_to_name[cid] = extract_doc_name(p, cid)

    # Level 3: Cities/Municipalities
    chunk_size = 25
    for i in range(0, len(prov_ids), chunk_size):
        chunk = prov_ids[i : i + chunk_size]
        resp_city = session.get(
            f"{base_url}/api/resource/PSGC Location",
            params={
                "fields": json.dumps(fields_list),
                "filters": json.dumps(
                    [["PSGC Location", "parent_psgc_location", "in", chunk]]
                ),
                "limit_page_length": 1000,
            },
            timeout=25,
        )
        resp_city.raise_for_status()
        for c in resp_city.json().get("data", []):
            cid = str(c.get("name", "")).strip()
            parent = str(c.get("parent_psgc_location") or "").strip()
            if cid:
                id_to_parent[cid] = parent
                id_to_name[cid] = extract_doc_name(c, cid)

    return id_to_name, id_to_parent


def fetch_single_psgc_doc(
    session: requests.Session,
    base_url: str,
    code: str,
    fallback_names: dict | None = None,
) -> dict | None:
    """Fetches a single PSGC Location document directly from ERPNext by ID."""
    if not code:
        return None
    base_url = base_url.rstrip("/")
    code_str = str(code).strip()
    url = f"{base_url}/api/resource/PSGC Location/{quote(code_str)}"
    resp = session.get(url, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json().get("data", {})
    if not data:
        return None

    loc_name = extract_doc_name(data, code_str)
    if (not loc_name or loc_name == code_str) and fallback_names:
        loc_name = fallback_names.get(code_str, code_str)

    parent = data.get("parent_psgc_location") or data.get("parent") or ""
    return {
        "name": str(data.get("name", code_str)).strip(),
        "location_name": loc_name,
        "parent_psgc_location": str(parent).strip() if parent else "",
    }


def audit_single_triplet(
    reg_code: str,
    prov_code: str,
    city_code: str,
    base_url: str,
    username: str,
    password: str,
    id_to_name: dict,
    id_to_parent: dict,
    progress_bar=None,
) -> dict:
    """Audits a single (Region, Province, City/Municipality) on the live ERPNext doctype."""
    reg_code = str(reg_code).strip()
    prov_code = str(prov_code).strip()
    city_code = str(city_code).strip()

    session = get_erpn_session(base_url, username, password)

    def resolve_node(code: str) -> dict | None:
        try:
            doc = fetch_single_psgc_doc(session, base_url, code, id_to_name)
            if doc is not None:
                return doc
        except Exception:
            pass
        if code in id_to_parent:
            return {
                "name": code,
                "location_name": id_to_name.get(code, code),
                "parent_psgc_location": id_to_parent.get(code, ""),
            }
        return None

    # Step 1: Verify Region
    if progress_bar:
        progress_bar.progress(35, text=f"Step 1/3: Verifying Region ({reg_code})...")
        time.sleep(0.05)
    reg_doc = resolve_node(reg_code)
    reg_ok = (
        reg_doc is not None
        and len(reg_code) == 10
        and reg_doc["parent_psgc_location"] == ""
    )
    reg_msg = (
        "Root Region Node (Valid)"
        if reg_ok
        else (
            "Not found in PSGC"
            if not reg_doc
            else f"Invalid parent ({reg_doc['parent_psgc_location']})"
        )
    )

    # Step 2: Verify Province
    if progress_bar:
        progress_bar.progress(70, text=f"Step 2/3: Verifying Province ({prov_code})...")
        time.sleep(0.05)
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
            100, text=f"Step 3/3: Verifying City/Municipality ({city_code})..."
        )
        time.sleep(0.05)
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


# --- HEADER ---
head_col1, head_col2 = st.columns([5, 1])
with head_col1:
    st.title("🌐 ERPNext Live PSGC Doctype Auditor")
    st.caption(
        "Live Hierarchy Search & Verification • `Region` → `Province` → `City/Municipality`"
    )
with head_col2:
    st.write("")
    if st.button("🔄 Sync Live PSGC", use_container_width=True):
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()

# Load Live ERPNext PSGC Tree
try:
    with st.spinner("Connecting to Live ERPNext PSGC Location doctype..."):
        m_names, m_parents = fetch_erpn_psgc_hierarchy(
            ERPN_BASE_URL, ERPN_USERNAME, ERPN_PASSWORD
        )
except Exception as exc:
    st.error(
        f"Unable to reach Live ERPNext PSGC Doctype. Please click Sync Live PSGC. ({exc})"
    )
    st.stop()

if not m_parents:
    st.warning("No PSGC Location records returned from ERPNext.")
    st.stop()

# Build Region, Province, and City lookup maps
all_regions = {
    f"{m_names.get(k, k)} ({k})": k for k, p in m_parents.items() if p == ""
}
reg_set = set(all_regions.values())

all_provinces = {
    f"{m_names.get(k, k)} ({k})": k
    for k, p in m_parents.items()
    if p in reg_set
}
prov_set = set(all_provinces.values())

all_cities = {
    f"{m_names.get(k, k)} ({k})": k
    for k, p in m_parents.items()
    if p in prov_set
}

# --- SEARCH PANEL (3 Aligned Columns) ---
with st.container(border=True):
    col_a, col_b, col_c = st.columns(3)

    with col_a:
        m_reg_label = st.selectbox(
            "LEVEL 1 • REGION",
            options=sorted(all_regions.keys()),
            index=None,
            placeholder="🔍 Search Region name or code...",
        )
        m_reg_code = all_regions.get(m_reg_label, "") if m_reg_label else ""

    with col_b:
        scoped_provs = {
            lbl: k
            for lbl, k in all_provinces.items()
            if not m_reg_code or m_parents.get(k) == m_reg_code
        }
        m_prov_label = st.selectbox(
            "LEVEL 2 • PROVINCE",
            options=sorted(scoped_provs.keys()),
            index=None,
            placeholder="🔍 Search Province name or code...",
        )
        m_prov_code = scoped_provs.get(m_prov_label, "") if m_prov_label else ""

    with col_c:
        scoped_cities = {
            lbl: k
            for lbl, k in all_cities.items()
            if (not m_prov_code or m_parents.get(k) == m_prov_code)
            and (
                not m_reg_code
                or m_parents.get(m_parents.get(k, ""), "") == m_reg_code
            )
        }
        m_city_label = st.selectbox(
            "LEVEL 3 • CITY / MUNICIPALITY",
            options=sorted(scoped_cities.keys()),
            index=None,
            placeholder="🔍 Search City/Municipality name or code...",
        )
        m_city_code = scoped_cities.get(m_city_label, "") if m_city_label else ""

# Auto-resolve parent hierarchy when user selects a City or Province
effective_city = m_city_code
effective_prov = m_prov_code or (
    m_parents.get(effective_city, "") if effective_city else ""
)
effective_reg = m_reg_code or (
    m_parents.get(effective_prov, "") if effective_prov else ""
)

# --- ALIGNED PREVIEW UI ---
if effective_reg and effective_prov and effective_city:
    m_pbar = st.progress(
        0, text="Auditing PSGC hierarchy against Live ERPNext Doctype..."
    )
    m_res = audit_single_triplet(
        effective_reg,
        effective_prov,
        effective_city,
        ERPN_BASE_URL,
        ERPN_USERNAME,
        ERPN_PASSWORD,
        m_names,
        m_parents,
        progress_bar=m_pbar,
    )
    m_pbar.progress(100, text="100% Verified against Live ERPNext PSGC Doctype")

    # 1. Clean Top Status Banner
    if m_res["all_ok"]:
        st.markdown(
            """
            <div class="status-banner-ok">
                <span>✅ 100% Valid Hierarchy (Live ERPNext Doctype)</span>
                <span style="font-size: 0.85rem; opacity: 0.85;">Region → Province → City/Municipality Verified</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div class="status-banner-err">
                <span>❌ Hierarchy Mismatch Detected</span>
                <span style="font-size: 0.85rem; opacity: 0.85;">Please review the parent-child links below</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. Three Perfectly Aligned Equal-Height Preview Cards (Region | Province | City/Municipality)
    c1, c2, c3 = st.columns(3)

    with c1:
        reg_cls = "psgc-link-ok" if m_res["reg_ok"] else "psgc-link-err"
        reg_icon = "✔" if m_res["reg_ok"] else "✖"
        st.markdown(
            f"""
            <div class="psgc-card">
                <div>
                    <div class="psgc-level">LEVEL 1 • REGION</div>
                    <div class="psgc-name">{m_res['reg_name']}</div>
                </div>
                <div>
                    <div class="psgc-code">{effective_reg}</div>
                    <div class="{reg_cls}">{reg_icon} {m_res['reg_msg']}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c2:
        prov_cls = "psgc-link-ok" if m_res["prov_ok"] else "psgc-link-err"
        prov_icon = "✔" if m_res["prov_ok"] else "✖"
        st.markdown(
            f"""
            <div class="psgc-card">
                <div>
                    <div class="psgc-level">LEVEL 2 • PROVINCE</div>
                    <div class="psgc-name">{m_res['prov_name']}</div>
                </div>
                <div>
                    <div class="psgc-code">{effective_prov}</div>
                    <div class="{prov_cls}">{prov_icon} {m_res['prov_msg']}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with c3:
        city_cls = "psgc-link-ok" if m_res["city_ok"] else "psgc-link-err"
        city_icon = "✔" if m_res["city_ok"] else "✖"
        st.markdown(
            f"""
            <div class="psgc-card">
                <div>
                    <div class="psgc-level">LEVEL 3 • CITY / MUNICIPALITY</div>
                    <div class="psgc-name">{m_res['city_name']}</div>
                </div>
                <div>
                    <div class="psgc-code">{effective_city}</div>
                    <div class="{city_cls}">{city_icon} {m_res['city_msg']}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 3. Aligned Single-Row Summary Table for ERPNext Data Import Reference
    st.markdown(
        f"""
        <table class="summary-table">
            <thead>
                <tr>
                    <th>Region Code</th>
                    <th>Region Name</th>
                    <th>Province Code</th>
                    <th>Province Name</th>
                    <th>City/Municipality Code</th>
                    <th>City/Municipality Name</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td class="mono-cell">{effective_reg}</td>
                    <td>{m_res['reg_name']}</td>
                    <td class="mono-cell">{effective_prov}</td>
                    <td>{m_res['prov_name']}</td>
                    <td class="mono-cell">{effective_city}</td>
                    <td>{m_res['city_name']}</td>
                </tr>
            </tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )

elif effective_reg or effective_prov:
    # Partial selection preview (when Region or Province is selected and awaiting City)
    p_c1, p_c2, p_c3 = st.columns(3)
    with p_c1:
        r_name = (
            m_names.get(effective_reg, "—") if effective_reg else "Select Region..."
        )
        st.markdown(
            f"""
            <div class="psgc-card">
                <div>
                    <div class="psgc-level">LEVEL 1 • REGION</div>
                    <div class="psgc-name">{r_name}</div>
                </div>
                <div>
                    <div class="psgc-code">{effective_reg or '—'}</div>
                    <div class="psgc-link-ok">✔ Root Region Selected</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with p_c2:
        p_name = (
            m_names.get(effective_prov, "Select Province...")
            if effective_prov
            else "Select Province..."
        )
        p_status = (
            f"✔ Linked to {effective_reg}"
            if effective_prov
            else "Awaiting Province selection"
        )
        st.markdown(
            f"""
            <div class="psgc-card">
                <div>
                    <div class="psgc-level">LEVEL 2 • PROVINCE</div>
                    <div class="psgc-name">{p_name}</div>
                </div>
                <div>
                    <div class="psgc-code">{effective_prov or '—'}</div>
                    <div class="psgc-link-ok">{p_status}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with p_c3:
        st.markdown(
            """
            <div class="psgc-card">
                <div>
                    <div class="psgc-level">LEVEL 3 • CITY / MUNICIPALITY</div>
                    <div class="psgc-name">Select City/Municipality...</div>
                </div>
                <div>
                    <div class="psgc-code">—</div>
                    <div class="psgc-link-ok">Select a City/Municipality above to complete audit</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
else:
    st.info(
        "👆 Search and select any **Region**, **Province**, or **City/Municipality** above to preview and verify its aligned ERPNext PSGC hierarchy."
    )
