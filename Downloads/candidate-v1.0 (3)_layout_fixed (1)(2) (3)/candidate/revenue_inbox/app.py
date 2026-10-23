
import json
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Revenue Intelligence Inbox",
    page_icon="📊",
    layout="wide",
)

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR.parent / "growth_associate" / "data"
OUTPUT_DIR = APP_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

STATE_FILE = OUTPUT_DIR / "review_state.json"

ROUTES = [
    "Sales coaching",
    "Deal next steps",
    "Customer follow-up",
    "Internal / vendor",
    "Human review",
    "No transcript",
]


def read_json(path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def batch_records(filename):
    path = DATA_DIR / filename
    if not path.exists():
        st.error(f"Missing source file: {path}")
        st.stop()
    data = read_json(path)
    return data, data.get("records", [])


def record_key(record):
    return (
        str(record.get("source_type", "")),
        str(record.get("source_id", "")),
    )


def transcript_text(record):
    payload = record.get("transcript_payload") or {}
    turns = payload.get("turns") or []
    return "\n".join(
        f"{turn.get('speaker', 'Unknown')} "
        f"[{turn.get('timecode', '')}]: {turn.get('text', '')}"
        for turn in turns
        if turn.get("text")
    )


def load_state():
    if STATE_FILE.exists():
        try:
            return read_json(STATE_FILE)
        except (json.JSONDecodeError, OSError):
            pass
    return {"records": {}, "manual_routes": {}}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def import_batch(state, records):
    added = 0
    updated = 0
    unchanged = 0

    for record in records:
        key = record_key(record)
        key_string = "|".join(key)

        if not all(key):
            continue

        old = state["records"].get(key_string)

        if old is None:
            state["records"][key_string] = record
            added += 1
        elif record != old:
            # The supplied update is authoritative for source data.
            state["records"][key_string] = record
            updated += 1
        else:
            unchanged += 1

    save_state(state)
    return added, updated, unchanged


def suggested_route(record):
    text = transcript_text(record).lower()

    if not text:
        return "No transcript"

    customer_signals = [
        "customer", "issue", "problem", "workaround",
        "upload", "processing", "invoice", "payment",
    ]

    if any(signal in text for signal in customer_signals):
        return "Customer follow-up"

    if "next step" in text or "follow up" in text:
        return "Deal next steps"

    if record.get("source_type") == "call":
        return "Sales coaching"

    return "Human review"


def prepare_rows(state):
    rows = []

    for key_string, record in state["records"].items():
        payload = record.get("payload") or {}
        text = transcript_text(record)
        manual = state["manual_routes"].get(key_string)

        if manual:
            route = manual
        else:
            route = suggested_route(record)

        rows.append({
            "record_ref": record.get("record_ref", ""),
            "source_type": record.get("source_type", ""),
            "source_id": record.get("source_id", ""),
            "record_key": key_string,
            "title": payload.get("title")
                or payload.get("rep_name")
                or record.get("record_ref", ""),
            "person": payload.get("rep_name")
                or payload.get("organizer_email")
                or "Unknown",
            "timestamp": payload.get("started_at")
                or payload.get("starts_at", ""),
            "duration_seconds": payload.get("duration_seconds"),
            "has_transcript": bool(text.strip()),
            "transcript": text,
            "route": route,
            "manual_override": bool(manual),
            "review_state": (
                "Needs review: updated transcript"
                if record.get("_needs_review")
                else (
                    "Ready for review" if text
                    else "No usable transcript"
                )
            ),
            "payload": payload,
        })

    return pd.DataFrame(rows)


if "review_state" not in st.session_state:
    st.session_state.review_state = load_state()

state = st.session_state.review_state

st.title("Revenue Intelligence Inbox")
st.caption(
    "Sales activity · Evidence-based routing · Review and exports"
)

initial_data, initial_records = batch_records("01_initial.json")
update_data, update_records = batch_records("02_update.json")

with st.sidebar:
    st.header("Import controls")

    if st.button("Import initial batch", use_container_width=True):
        result = import_batch(state, initial_records)
        st.success(
            f"Added {result[0]} · Updated {result[1]} · "
            f"Unchanged {result[2]}"
        )
        st.rerun()

    if st.button("Import initial batch again", use_container_width=True):
        result = import_batch(state, initial_records)
        st.success(
            f"Added {result[0]} · Updated {result[1]} · "
            f"Unchanged {result[2]}"
        )
        st.rerun()

    if st.button("Import update batch", use_container_width=True):
        result = import_batch(state, update_records)

        # Updated records must be reviewed again.
        for record in update_records:
            key_string = "|".join(record_key(record))
            saved = state["records"].get(key_string, {})
            saved["_needs_review"] = True
            state["records"][key_string] = saved

        save_state(state)
        st.success(
            f"Added {result[0]} · Updated {result[1]} · "
            f"Unchanged {result[2]}"
        )
        st.rerun()

    if st.button("Reset demo", use_container_width=True):
        state = {"records": {}, "manual_routes": {}}
        st.session_state.review_state = state
        save_state(state)
        st.rerun()

    st.caption(
        "Reset clears local demo state and manual corrections. "
        "Use it only when restarting the replay test."
    )

df = prepare_rows(state)

total = len(df)
transcripts = int(df["has_transcript"].sum()) if total else 0
manual_count = int(df["manual_override"].sum()) if total else 0
needs_review = int(
    df["review_state"].str.contains("Needs review").sum()
) if total else 0

a, b, c, d = st.columns(4)
a.metric("Unique source records", total)
b.metric("Usable transcripts", f"{transcripts}/18")
c.metric("Manual route corrections", manual_count)
d.metric("Updated / needs review", needs_review)

st.caption(
    "Transcript coverage uses 18 supplied conversation texts as the "
    "expected final set. Records are identified by source_type + source_id."
)

if total:
    st.subheader("Activity overview")

    left, right = st.columns(2)

    with left:
        st.markdown("**Records by source type**")
        st.bar_chart(df["source_type"].value_counts())

    with right:
        st.markdown("**Records by person / organiser**")
        person_counts = df["person"].value_counts().head(12)
        st.bar_chart(person_counts)

st.subheader("Conversation inbox")

search = st.text_input(
    "Search by record reference, title, person or transcript"
)

f1, f2, f3 = st.columns(3)

with f1:
    source_filter = st.selectbox(
        "Source type",
        ["All", "call", "meeting"],
    )

with f2:
    route_options = ["All"] + ROUTES
    route_filter = st.selectbox("Route", route_options)

with f3:
    transcript_filter = st.selectbox(
        "Transcript status",
        ["All", "Available", "Missing"],
    )

filtered = df.copy() if total else df

if total and source_filter != "All":
    filtered = filtered[filtered["source_type"] == source_filter]

if total and route_filter != "All":
    filtered = filtered[filtered["route"] == route_filter]

if total and transcript_filter == "Available":
    filtered = filtered[filtered["has_transcript"]]

if total and transcript_filter == "Missing":
    filtered = filtered[~filtered["has_transcript"]]

if total and search:
    searchable = (
        filtered["record_ref"].astype(str)
        + " " + filtered["title"].astype(str)
        + " " + filtered["person"].astype(str)
        + " " + filtered["transcript"].astype(str)
    )
    filtered = filtered[
        searchable.str.contains(search, case=False, na=False)
    ]

if filtered.empty:
    st.info("No records yet. Import the initial batch from the sidebar.")
else:
    st.dataframe(
        filtered[[
            "record_ref",
            "source_type",
            "title",
            "person",
            "has_transcript",
            "route",
            "manual_override",
            "review_state",
        ]],
        use_container_width=True,
        hide_index=True,
    )

    chosen_ref = st.selectbox(
        "Open a conversation",
        filtered["record_ref"].tolist(),
    )

    selected = filtered[
        filtered["record_ref"] == chosen_ref
    ].iloc[0]

    st.markdown(f"### {selected['record_ref']} — {selected['title']}")
    st.write("Source:", selected["source_type"])
    st.write("Person / organiser:", selected["person"])
    st.write("Review state:", selected["review_state"])

    if selected["transcript"]:
        with st.expander("Read transcript evidence", expanded=True):
            st.text(selected["transcript"])
    else:
        st.warning(
            "No usable supplied transcript. Do not infer conversation "
            "content from missing text."
        )

    current_route = selected["route"]
    route_index = ROUTES.index(current_route) if current_route in ROUTES else 0

    new_route = st.selectbox(
        "Route decision",
        ROUTES,
        index=route_index,
        key=f"route_{selected['record_key']}",
    )

    if st.button("Save manual route correction"):
        state["manual_routes"][selected["record_key"]] = new_route

        if selected["record_key"] in state["records"]:
            state["records"][selected["record_key"]].pop(
                "_needs_review", None
            )

        save_state(state)
        st.success(
            f"Saved manual route for {selected['record_ref']}: "
            f"{new_route}"
        )
        st.rerun()

    if selected["record_key"] in state["manual_routes"]:
        if st.button("Reset to suggested route"):
            del state["manual_routes"][selected["record_key"]]
            save_state(state)
            st.rerun()


st.subheader("Exports")

df = prepare_rows(state)

if not df.empty:
    export_rows = []
    output_root = OUTPUT_DIR / "conversations"
    output_root.mkdir(parents=True, exist_ok=True)

    for _, row in df.iterrows():
        route_folder = (
            str(row["route"])
            .replace("/", "_")
            .replace(" ", "_")
        )
        route_dir = output_root / route_folder
        route_dir.mkdir(parents=True, exist_ok=True)

        output_path = route_dir / f"{row['record_ref']}.json"

        conversation = {
            "record_ref": row["record_ref"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "title": row["title"],
            "person": row["person"],
            "timestamp": row["timestamp"],
            "duration_seconds": row["duration_seconds"],
            "route": row["route"],
            "manual_override": bool(row["manual_override"]),
            "review_state": row["review_state"],
            "has_transcript": bool(row["has_transcript"]),
            "transcript": row["transcript"],
            "source_payload": row["payload"],
        }

        output_path.write_text(
            json.dumps(conversation, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        export_rows.append({
            "record_ref": row["record_ref"],
            "source_type": row["source_type"],
            "source_id": row["source_id"],
            "route": row["route"],
            "manual_override": bool(row["manual_override"]),
            "review_state": row["review_state"],
            "has_transcript": bool(row["has_transcript"]),
            "output_path": str(output_path.relative_to(OUTPUT_DIR)),
        })

    manifest = pd.DataFrame(export_rows)

    st.success(
        f"Generated {len(export_rows)} conversation JSON files "
        f"inside {output_root}"
    )

    st.download_button(
        "Download CSV manifest",
        manifest.to_csv(index=False),
        file_name="manifest.csv",
        mime="text/csv",
    )

    st.download_button(
        "Download JSON manifest",
        json.dumps(export_rows, ensure_ascii=False, indent=2),
        file_name="manifest.json",
        mime="application/json",
    )

    st.download_button(
        "Download records CSV",
        df.drop(columns=["payload", "transcript"]).to_csv(index=False),
        file_name="reviewed_records.csv",
        mime="text/csv",
    )

st.caption(
    "Routing suggestions are deterministic. Review and correct routes "
    "manually; new or changed transcripts require human review."
)

