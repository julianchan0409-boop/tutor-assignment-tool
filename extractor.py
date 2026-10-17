import io
import json
import os
from typing import List, Literal, Optional, Tuple, Union
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


class QuestionPart(BaseModel):
    label: str = Field(
        description="Subpart label such as '(a)', '(b)', '(i)', or '(A)'"
    )
    text: str = Field(
        description="Clean LaTeX text for this subpart, using $...$ or $$...$$ for math."
    )
    marks: int = Field(
        default=1,
        ge=0,
        description="Marks allocated to this subpart (0 if it is an ungraded MCQ choice option).",
    )


class ExtractedQuestion(BaseModel):
    section: Literal["Section A", "Section B"] = Field(
        description="'Section A' for Multiple Choice questions, 'Section B' for Short Answer / free-response questions."
    )
    marks: int = Field(
        default=1,
        ge=1,
        description="Total marks for the question. If parts have marks, this should equal the sum of part marks.",
    )
    body: str = Field(
        description="Main stem/body of the question in clean LaTeX using $...$ or $$...$$ for math expressions. Do not include \\question command."
    )
    parts: List[QuestionPart] = Field(
        default_factory=list,
        description="List of subparts (e.g., (a), (b)) or MCQ choices if structured as parts.",
    )
    needs_working_lines: bool = Field(
        default=True,
        description="True if this is a calculation / short-answer question requiring ruled working space (\\fillwithlines).",
    )
    has_diagram: bool = Field(
        default=False,
        description="True if the source question contains a coordinate graph, geometry figure, or visual diagram.",
    )
    tikz_code: Optional[str] = Field(
        default=None,
        description=(
            "Complete compilable \\begin{tikzpicture}...\\end{tikzpicture} block (using pgfplots/tikz) "
            "recreating the diagram if feasible; null if there is no diagram or if the diagram is too complex for TikZ."
        ),
    )
    diagram_bbox: Optional[List[int]] = Field(
        default=None,
        description=(
            "Optional bounding box [ymin, xmin, ymax, xmax] normalized to 0-1000 of the diagram region "
            "in the source image, used for cropping when tikz_code is null or fallback image is requested."
        ),
    )


class QuestionBatch(BaseModel):
    questions: List[ExtractedQuestion] = Field(
        description="One or more math questions extracted from the uploaded image or PDF page."
    )


SYSTEM_PROMPT = r"""You are an expert mathematics curriculum developer and LaTeX typesetter specializing in the LaTeX `exam` document class.

Your task is to analyze raw screenshots, photos, or scanned PDFs of math questions and convert each question into clean, standardized JSON ready for assembly into an exam paper.

Follow these strict rules:
1. **Math Transcription**:
   - Transcribe all mathematical expressions, variables, equations, and units into clean LaTeX math notation using `$...$` for inline math and `$$...$$` (or `\\[ ... \\]`) for display math.
   - Fix any OCR artifacts, blurry symbols, or inconsistent spacing from the source scan.
   - Do NOT include outer `\\question` or `\\begin{parts}` wrappers inside `body` or `parts[].text`; those are injected by the compiler.

2. **Section Classification**:
   - Classify Multiple Choice questions as `"Section A"`.
   - Classify Short Answer, calculation, proof, or multi-part structured questions as `"Section B"`.
   - For Multiple Choice questions (`"Section A"`), if choices A, B, C, D are present, either place them in `parts` with labels `"(A)"`, `"(B)"`, `"(C)"`, `"(D)"` and `marks: 0`, or format them cleanly in `body`.

3. **Marks & Subparts**:
   - Extract point values (`marks`) from annotations like `[3]`, `(2 marks)`, `3 pts`, etc. If no marks are shown, infer a reasonable point allocation (e.g., 1 mark for MCQ, 2-4 marks for short answer steps).
   - Put subparts `(a)`, `(b)`, `(c)` into the `parts` array with their respective `label`, `text`, and `marks`.
   - Set top-level `marks` to the total marks of the question (equal to the sum of `parts[].marks` when subparts carry marks).

4. **Working Space (`needs_working_lines`)**:
   - Set `needs_working_lines` to `true` for calculation, algebraic manipulation, or short-answer questions (`Section B`).
   - Set `needs_working_lines` to `false` for pure Multiple Choice questions (`Section A`) unless working space is explicitly needed.

5. **Diagrams & TikZ Generation (`has_diagram`, `tikz_code`, `diagram_bbox`)**:
   - Set `has_diagram` to `true` if the question includes a coordinate axes plot, function graph, geometric figure, vector diagram, or schematic.
   - **Favor TikZ**: Whenever feasible (coordinate axes, function plots, triangles, circles, angles, number lines, standard geometry), generate clean, self-contained `\begin{tikzpicture}...\end{tikzpicture}` code (using `tikz` or `pgfplots` with `\begin{axis}...\end{axis}`) in `tikz_code`.
   - Always provide `diagram_bbox` as `[ymin, xmin, ymax, xmax]` (integers scaled 0 to 1000) tightly bounding the visual diagram in the source image so the application can crop and reference the original image if TikZ is `null` or if the tutor switches to image mode.
"""


def guess_mime_type(file_bytes: bytes, filename: str = "") -> str:
    """Determine MIME type from filename or magic bytes."""
    lower = filename.lower()
    if lower.endswith(".pdf") or file_bytes[:4] == b"%PDF":
        return "application/pdf"
    if lower.endswith(".png") or file_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")) or file_bytes[:2] == b"\xff\xd8":
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/png"


def crop_diagram_from_image(
    image_bytes: bytes,
    bbox_1000: Optional[List[int]] = None,
    padding_pct: float = 0.02,
) -> Optional[bytes]:
    """
    Crop a diagram region from image bytes using normalized [ymin, xmin, ymax, xmax] (0-1000) coordinates.
    Returns PNG bytes of the cropped diagram, or None if image_bytes is not a raster image.
    """
    if not image_bytes or image_bytes[:4] == b"%PDF":
        return None

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            if bbox_1000 and len(bbox_1000) == 4:
                ymin, xmin, ymax, xmax = bbox_1000
                ymin, ymax = sorted([max(0, min(1000, ymin)), max(0, min(1000, ymax))])
                xmin, xmax = sorted([max(0, min(1000, xmin)), max(0, min(1000, xmax))])

                pad_x = int(width * padding_pct)
                pad_y = int(height * padding_pct)

                left = max(0, int((xmin / 1000.0) * width) - pad_x)
                top = max(0, int((ymin / 1000.0) * height) - pad_y)
                right = min(width, int((xmax / 1000.0) * width) + pad_x)
                bottom = min(height, int((ymax / 1000.0) * height) + pad_y)

                if right > left + 10 and bottom > top + 10:
                    cropped = img.crop((left, top, right, bottom))
                else:
                    cropped = img.copy()
            else:
                cropped = img.copy()

            out = io.BytesIO()
            cropped.convert("RGB").save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return None


def extract_questions_from_bytes(
    file_bytes: bytes,
    filename: str = "upload.png",
    api_key: Optional[str] = None,
    model_name: str = "gemini-2.5-flash",
) -> List[dict]:
    """
    Call Google Gemini Vision/Multimodal API on image or PDF bytes and return
    a list of standardized question dicts matching the required schema.
    """
    resolved_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not resolved_key:
        raise ValueError(
            "Missing Gemini API Key. Please provide your key in the sidebar or set GEMINI_API_KEY."
        )

    client = genai.Client(api_key=resolved_key)
    mime_type = guess_mime_type(file_bytes, filename)

    part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    prompt = (
        "Extract all math assessment questions from this file into the standardized JSON schema. "
        "Ensure all math uses clean LaTeX ($...$ or $$...$$), subparts use (a), (b), etc., "
        "and generate compilable TikZ/pgfplots code for any coordinate axes or geometry diagrams whenever feasible."
    )

    response = client.models.generate_content(
        model=model_name,
        contents=[part, prompt],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=QuestionBatch,
            temperature=0.1,
        ),
    )

    raw_text = response.text or "{}"
    parsed_data = json.loads(raw_text)
    questions_raw = parsed_data.get("questions", [])

    # Fallback if a single question object was returned directly
    if isinstance(parsed_data, dict) and "questions" not in parsed_data and "body" in parsed_data:
        questions_raw = [parsed_data]

    results: List[dict] = []
    for q in questions_raw:
        validated = ExtractedQuestion.model_validate(q)
        q_dict = {
            "section": validated.section,
            "marks": validated.marks,
            "body": validated.body,
            "parts": [p.model_dump() for p in validated.parts],
            "needs_working_lines": validated.needs_working_lines,
            "has_diagram": validated.has_diagram,
            "tikz_code": validated.tikz_code,
            "diagram_bbox": validated.diagram_bbox,
        }
        results.append(q_dict)

    return results
