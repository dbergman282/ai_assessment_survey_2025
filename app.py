import streamlit as st
import pandas as pd
from supabase import create_client, Client

# --------------------------------------------------
# 0. Page config (wide layout)
# --------------------------------------------------
st.set_page_config(
    page_title="AI & Assessments – Faculty Portal",
    layout="wide",
)

# --------------------------------------------------
# 1. Supabase client (from secrets.toml)
# --------------------------------------------------

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]


@st.cache_resource
def get_supabase_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# Fixed assessment types (11 rows)
ASSESSMENT_TYPES = [
    "In-person exam/quiz, closed resources, no AI",
    "In-person exam/quiz, limited resources (notes/book), no AI",
    "In-person exam/quiz, open resources, AI allowed",
    "Online timed exam/quiz, closed resources, no AI",
    "Online timed exam/quiz, limited resources (notes/book), no AI",
    "Online timed exam/quiz, open resources, AI allowed",
    "Out-of-class untimed exam/quiz, closed resources, no AI",
    "Out-of-class untimed exam/quiz, limited resources (notes/book), no AI",
    "Out-of-class untimed exam/quiz, open resources, AI allowed",
    "In-person participation/presentations, no AI",
    "In-person participation/presentations, AI allowed",
]


# --------------------------------------------------
# 2. Session state init
# --------------------------------------------------

def init_session_state():
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False
    if "code" not in st.session_state:
        st.session_state["code"] = ""
    if "user_info" not in st.session_state:
        st.session_state["user_info"] = None


# --------------------------------------------------
# 3. Login view
# --------------------------------------------------

def login_view():
    st.title("AI & Assessments – Faculty Login")

    st.write("Please enter the code you were given to access the system.")

    code = st.text_input("Instructor code", type="password")

    if st.button("Log in"):
        code = code.strip()
        if not code:
            st.error("Please enter a code.")
            return

        try:
            supabase = get_supabase_client()
            res = supabase.table("instructors").select("code,name,email").eq("code", code).execute()
        except Exception as e:
            st.error(
                "There was a problem connecting to the database while checking your code. "
                "Please try again in a moment or contact David."
            )
            st.caption(f"Technical details (for admin): {e}")
            return

        if res.data:
            user = res.data[0]
            st.session_state["logged_in"] = True
            st.session_state["code"] = user["code"]
            st.session_state["user_info"] = {
                "name": user.get("name", ""),
                "email": user.get("email", ""),
            }
            st.rerun()
        else:
            st.error("Invalid code. Please check your code or contact the organizer.")


# --------------------------------------------------
# 4. Data helpers – Courses
# --------------------------------------------------

def load_courses_for_instructor(instructor_code: str):
    supabase = get_supabase_client()
    res = (
        supabase.table("courses")
        .select(
            "id, instructor_code, course_code, course_title, term, level, modality, approx_students, created_at"
        )
        .eq("instructor_code", instructor_code)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data or []


def add_course_for_instructor(
    instructor_code: str,
    course_code: str,
    course_title: str,
    term: str,
    level: str,
    modality: str,
    approx_students: int,
):
    supabase = get_supabase_client()
    supabase.table("courses").insert(
        {
            "instructor_code": instructor_code,
            "course_code": course_code,
            "course_title": course_title,
            "term": term,
            "level": level,
            "modality": modality,
            "approx_students": approx_students,
        }
    ).execute()


def update_course_for_instructor(
    course_id: str,
    instructor_code: str,
    course_title: str,
    term: str,
    level: str,
    modality: str,
    approx_students: int,
):
    supabase = get_supabase_client()
    supabase.table("courses").update(
        {
            "course_title": course_title,
            "term": term,
            "level": level,
            "modality": modality,
            "approx_students": approx_students,
        }
    ).eq("id", course_id).eq("instructor_code", instructor_code).execute()


def delete_course_for_instructor(course_id: str, instructor_code: str, course_code: str):
    supabase = get_supabase_client()
    # Delete assessments for this instructor + course
    supabase.table("assessments").delete().eq("instructor_code", instructor_code).eq(
        "course_code", course_code
    ).execute()
    # Delete the course itself
    supabase.table("courses").delete().eq("id", course_id).eq("instructor_code", instructor_code).execute()


# --------------------------------------------------
# 5. Data helpers – Assessments
# --------------------------------------------------

def load_assessments_for_course(instructor_code: str, course_code: str) -> pd.DataFrame:
    """
    Always return a DataFrame with all ASSESSMENT_TYPES as rows.
    If existing data exists, merge it in; otherwise start with defaults that sum to 100.
    """
    supabase = get_supabase_client()
    res = (
        supabase.table("assessments")
        .select("*")
        .eq("instructor_code", instructor_code)
        .eq("course_code", course_code)
        .execute()
    )
    data = res.data or []

    df_types = pd.DataFrame({"assessment_type": ASSESSMENT_TYPES})

    if not data:
        # Default: 100% of assessment on the first row, 0 elsewhere.
        df_types["percent_of_class_assessment"] = 0.0
        df_types.loc[0, "percent_of_class_assessment"] = 100.0
        df_types["ai_misuse_susceptibility"] = 0.0
        df_types["modification_level"] = 0.0
        return df_types

    df_db = pd.DataFrame(data)
    df_db = df_db[
        [
            "assessment_type",
            "percent_of_class_assessment",
            "ai_misuse_susceptibility",
            "modification_level",
        ]
    ]

    # BUG FIX: correct merge syntax (on="assessment_type")
    df = df_types.merge(df_db, on="assessment_type", how="left")
    return df


def save_assessments_for_course(instructor_code: str, course_code: str, df: pd.DataFrame):
    """
    Save all assessment rows for this course.
    Enforce that percent_of_class_assessment sums to 100 (with small tolerance).
    """
    supabase = get_supabase_client()
    df = df.copy()

    df["assessment_type"] = df["assessment_type"].fillna("").str.strip()
    df["percent_of_class_assessment"] = df["percent_of_class_assessment"].fillna(0)

    total = float(df["percent_of_class_assessment"].sum())

    # Validation for total 100
    if abs(total - 100.0) > 0.5:  # allow slight rounding error
        raise ValueError(
            f"The 'Percent of class assessment' values must sum to 100. "
            f"Right now they sum to {total:.1f}. Please adjust the numbers and try again."
        )

    df["ai_misuse_susceptibility"] = df["ai_misuse_susceptibility"].fillna(0)
    df["modification_level"] = df["modification_level"].fillna(0)

    cols = [
        "instructor_code",
        "course_code",
        "assessment_type",
        "percent_of_class_assessment",
        "ai_misuse_susceptibility",
        "modification_level",
    ]
    df["instructor_code"] = instructor_code
    df["course_code"] = course_code

    rows = df[cols].to_dict(orient="records")

    # Replace existing rows atomically-ish
    supabase.table("assessments").delete().eq("instructor_code", instructor_code).eq(
        "course_code", course_code
    ).execute()
    supabase.table("assessments").insert(rows).execute()


# --------------------------------------------------
# 6. Logged-in view
# --------------------------------------------------

def main_view():
    user = st.session_state["user_info"]
    instructor_code = st.session_state["code"]

    st.title("AI & Assessments – Faculty Portal")

    st.success(f"You are logged in as **{user['name']}**.")
    st.write(f"**Email:** {user['email']}")
    st.write(f"**Code:** `{instructor_code}`")

    # ---------- Instructions section ----------
    st.markdown("---")
    with st.expander("ℹ️ Instructions (please read first)", expanded=True):
        st.markdown(
            """
### What we’re asking you to do

Please use this portal to describe how you currently assess students in your courses and how those assessments relate to AI tools.

**For this survey, please:**

1. **Add a course entry for every distinct course you taught this academic year.**
2. **If you teach the same course in multiple modalities**  
   (e.g., in-person and online asynchronous),  
   create **a separate course entry for each modality**.
3. For each course entry:
   - Provide basic course information (title, term, modality, approximate enrollment).
   - Use the **Assessment structure** section to indicate:
     - How much each assessment type contributes to the **overall course grade**  
       (these percentages must sum to **100**).
     - How susceptible each assessment type is to **AI misuse**.
     - How much you have **modified** that component since the “AI revolution”  
       (0 = not at all, 100 = completely redesigned).
4. You can **return and edit** your entries at any time:
   - Use **“Your courses”** to review what you’ve entered.
   - Use **“Select a course to manage”** to switch which course you’re editing.
   - Remember to click **“Save assessments for this course”** when you change the matrix.

If something doesn’t fit perfectly (e.g., unusual assessments), please use the
closest category and interpret the questions in a way that makes sense for your course.
"""
        )

    st.markdown("---")

    # ---------- Collapsible Add Course (collapsed by default) ----------
    with st.expander("➕ Add a course", expanded=False):
        with st.form("add_course_form"):
            course_code = st.text_input("Course code (e.g., ACCT 2001)")
            course_title = st.text_input("Course title")
            col1, col2 = st.columns(2)
            with col1:
                term = st.text_input("Term (e.g., Fall 2025)")
                approx_students = st.number_input(
                    "Approximate number of students",
                    min_value=0,
                    max_value=1000,
                    step=1,
                    value=0,
                )
            with col2:
                level = st.selectbox(
                    "Level",
                    ["", "Undergraduate", "Graduate", "Other"],
                    index=0,
                )
                modality = st.selectbox(
                    "Modality",
                    ["", "In person", "Online asynchronous", "Online synchronous"],
                    index=0,
                )

            submitted = st.form_submit_button("Add course")

            if submitted:
                if not course_code.strip():
                    st.error("Course code is required.")
                else:
                    try:
                        add_course_for_instructor(
                            instructor_code=instructor_code,
                            course_code=course_code.strip(),
                            course_title=course_title.strip(),
                            term=term.strip(),
                            level=level.strip(),
                            modality=modality.strip(),
                            approx_students=int(approx_students),
                        )
                        st.success("Course added.")
                        st.rerun()
                    except Exception as e:
                        st.error(
                            "There was an error while saving this course. "
                            "Please try again, and if the problem persists, contact David."
                        )
                        st.caption(f"Technical details (for admin): {e}")

    st.markdown("---")

    # ---------- Load courses ----------
    try:
        courses = load_courses_for_instructor(instructor_code)
    except Exception as e:
        st.error(
            "There was an error loading your courses from the database. "
            "Please refresh the page or try again later."
        )
        st.caption(f"Technical details (for admin): {e}")
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()
        return

    if not courses:
        st.info("You haven't added any courses yet.")
        if st.button("Log out"):
            st.session_state.clear()
            st.rerun()
        return

    # ---------- Courses table (in its own expander) ----------
    with st.expander("📚 Your courses", expanded=False):
        st.subheader("Your courses")
        df_courses = pd.DataFrame(courses)
        cols = [
            c
            for c in [
                "course_code",
                "course_title",
                "term",
                "level",
                "modality",
                "approx_students",
                "created_at",
            ]
            if c in df_courses.columns
        ]
        st.dataframe(df_courses[cols], use_container_width=True)

    # ---------- Course selection OUTSIDE the expander ----------
    st.markdown("### Select a course to manage")

    options = {
        f"{c['course_code']} – {c.get('course_title', '') or ''}": c for c in courses
    }
    labels = list(options.keys())
    selected_label = st.selectbox("Select a course", labels)
    selected_course = options[selected_label]

    st.markdown(
        f"**Currently selected:** {selected_course['course_code']} – "
        f"{selected_course.get('course_title', '') or ''}"
    )

    st.markdown("---")

    # ---------- Edit course info (collapsed by default) ----------
    with st.expander("✏️ Edit selected course info", expanded=False):
        col_info, col_delete = st.columns([3, 1])

        with col_info:
            with st.form(f"edit_course_{selected_course['id']}"):
                st.text_input(
                    "Course code (fixed)",
                    value=selected_course["course_code"],
                    disabled=True,
                )
                course_title = st.text_input(
                    "Course title", value=selected_course.get("course_title", "") or ""
                )
                term = st.text_input(
                    "Term (e.g., Fall 2025)", value=selected_course.get("term", "") or ""
                )
                level = st.selectbox(
                    "Level",
                    ["", "Undergraduate", "Graduate", "Other"],
                    index=[
                        "",
                        "Undergraduate",
                        "Graduate",
                        "Other",
                    ].index(selected_course.get("level", "") or ""),
                )
                modality = st.selectbox(
                    "Modality",
                    ["", "In person", "Online asynchronous", "Online synchronous"],
                    index=[
                        "",
                        "In person",
                        "Online asynchronous",
                        "Online synchronous",
                    ].index(selected_course.get("modality", "") or ""),
                )
                approx_students = st.number_input(
                    "Approximate number of students",
                    min_value=0,
                    max_value=1000,
                    step=1,
                    value=int(selected_course.get("approx_students") or 0),
                )

                update_clicked = st.form_submit_button("Save course changes")

            if update_clicked:
                try:
                    update_course_for_instructor(
                        course_id=selected_course["id"],
                        instructor_code=instructor_code,
                        course_title=course_title.strip(),
                        term=term.strip(),
                        level=level.strip(),
                        modality=modality.strip(),
                        approx_students=int(approx_students),
                    )
                    st.success("Course information updated.")
                    st.rerun()
                except Exception as e:
                    st.error(
                        "There was an error updating this course. "
                        "Please try again, and if the problem continues, contact David."
                    )
                    st.caption(f"Technical details (for admin): {e}")

        with col_delete:
            st.markdown("##### Danger zone")
            if st.button("Delete this course"):
                try:
                    delete_course_for_instructor(
                        course_id=selected_course["id"],
                        instructor_code=instructor_code,
                        course_code=selected_course["course_code"],
                    )
                    st.success("Course (and its assessments) deleted.")
                    st.rerun()
                except Exception as e:
                    st.error(
                        "There was an error deleting this course. "
                        "Please try again, and if the problem continues, contact David."
                    )
                    st.caption(f"Technical details (for admin): {e}")

    st.markdown("---")

    # ---------- Assessments (collapsed by default) ----------
    with st.expander("📊 Assessment structure for this course", expanded=False):
        st.subheader("Assessment structure for this course")

        st.markdown(
            """
For each assessment type below, please provide:

- **Estimate percent of class assessment** (must sum to 100 across all rows)
- **Estimate susceptibility to AI misuse** (0–100)
- **Level of modification post-AI revolution** (0 = nothing, 100 = fully changed)

The table starts with a simple default: **100%** of assessment weight on the first row and **0%** on all others.
You can adjust from there.
"""
        )

        try:
            assessments_df = load_assessments_for_course(
                instructor_code, selected_course["course_code"]
            )
        except Exception as e:
            st.error(
                "There was an error loading the assessment data for this course. "
                "Please refresh the page or try another course."
            )
            st.caption(f"Technical details (for admin): {e}")
            assessments_df = pd.DataFrame({"assessment_type": ASSESSMENT_TYPES})

        current_total = (
            assessments_df["percent_of_class_assessment"].fillna(0).sum()
            if "percent_of_class_assessment" in assessments_df.columns
            else 0
        )
        st.markdown(
            f"**Current total percent of class assessment: {current_total:.1f} (must equal 100)**"
        )

        edited_df = st.data_editor(
            assessments_df,
            num_rows=len(ASSESSMENT_TYPES),
            use_container_width=True,
            disabled=["assessment_type"],
            column_config={
                "assessment_type": st.column_config.TextColumn("Assessment type"),
                "percent_of_class_assessment": st.column_config.NumberColumn(
                    "Percent of class assessment",
                    min_value=0,
                    max_value=100,
                ),
                "ai_misuse_susceptibility": st.column_config.NumberColumn(
                    "AI misuse susceptibility (0–100)", min_value=0, max_value=100
                ),
                "modification_level": st.column_config.NumberColumn(
                    "Modification level post-AI (0–100)", min_value=0, max_value=100
                ),
            },
            key=f"assessments_editor_{selected_course['id']}",
        )

        if st.button("Save assessments for this course"):
            try:
                save_assessments_for_course(
                    instructor_code, selected_course["course_code"], edited_df
                )
                st.success("Assessments saved.")
            except ValueError as ve:
                st.error(str(ve))
                st.info(
                    "Tip: Check the 'Percent of class assessment' column and make sure "
                    "the values across all rows add up to exactly 100."
                )
            except Exception as e:
                st.error(
                    "There was an unexpected error while saving your assessments. "
                    "You can try again, and if the problem continues, please contact David."
                )
                st.caption(f"Technical details (for admin): {e}")

    st.markdown("---")

    if st.button("Log out"):
        st.session_state.clear()
        st.rerun()


# --------------------------------------------------
# 7. App entry point
# --------------------------------------------------

def main():
    init_session_state()

    with st.sidebar:
        if st.session_state["logged_in"]:
            st.write(f"Logged in as: {st.session_state['user_info']['name']}")
            if st.button("Log out", key="sidebar_logout"):
                st.session_state.clear()
                st.rerun()
        else:
            st.write("Not logged in")

    if not st.session_state["logged_in"]:
        login_view()
    else:
        main_view()


if __name__ == "__main__":
    main()
