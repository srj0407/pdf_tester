from flask import Flask, request, jsonify
import pdfplumber
import re
import sys
import os
from pdf2image import convert_from_path
import pytesseract
from PIL import Image, ImageEnhance

app = Flask(__name__)

def extract_text_from_pdf(pdf_path):
    """
    Input:
        pdf_path (str): The file path to the PDF.
    Output:
        A string containing the text extracted via pdfplumber.
    Function description:
        Opens the PDF using pdfplumber and concatenates text from all pages.
    """
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        print(f"Error reading PDF file '{pdf_path}': {e}")
        sys.exit(1)
    return text

def preprocess_image(image):
    """
    Input:
        image (PIL.Image.Image): The image to be processed.
    Output:
        A processed image in binary format.
    Function description:
        Converts the image to grayscale, enhances contrast, and applies a binary threshold.
    """
    image = image.convert('L')
    image = ImageEnhance.Contrast(image).enhance(2)
    image = image.point(lambda x: 0 if x < 140 else 255, '1')
    return image

def extract_text_with_ocr(pdf_path):
    """
    Input:
        pdf_path (str): The file path to the PDF.
    Output:
        A string containing the text extracted via OCR.
    Function description:
        Converts PDF pages to high-resolution images (400 dpi), pre-processes each image,
        and extracts text using pytesseract with custom configuration.
    """
    text = ""
    try:
        pages = convert_from_path(pdf_path, dpi=400)
        custom_config = r'--oem 3 --psm 4'
        for page in pages:
            preprocessed_page = preprocess_image(page)
            page_text = pytesseract.image_to_string(preprocessed_page, config=custom_config)
            text += page_text + "\n"
    except Exception as e:
        print(f"Error during OCR processing of PDF file '{pdf_path}': {e}")
        sys.exit(1)
    return text

def extract_section(text, section_heading):
    """
    Input:
        text (str): The complete text from which to extract a section.
        section_heading (str): The heading marking the start of the section.
    Output:
        A string containing the extracted section, or None if not found.
    Function description:
        Uses a regular expression to capture text after the section heading until a new header (a line starting with a capital letter) or end-of-text.
    """
    pattern = rf"(?i){re.escape(section_heading)}\s*[:\n]+\s*(.*?)(?=\n[A-Z][a-z]|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None

def extract_section_with_boundaries(text, start_heading, end_boundaries):
    """
    Input:
        text (str): The complete text.
        start_heading (str): The heading marking the beginning of the section.
        end_boundaries (list of str): A list of strings that indicate where the section should end.
    Output:
        A string containing the extracted section, or None if not found.
    Function description:
        Captures text from the start_heading until one of the end_boundaries (each preceded by a newline) or end-of-text.
    """
    boundary_pattern = "|".join([rf"\n{re.escape(b)}" for b in end_boundaries])
    pattern = rf"(?i){re.escape(start_heading)}\s*[:\n]+\s*(.*?)(?={boundary_pattern}|\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None

def extract_section_multiple(text, possible_headings):
    """
    Input:
        text (str): The complete text.
        possible_headings (list of str): Candidate headings for the desired section.
    Output:
        A string containing the extracted section from the first matching heading, or None.
    Function description:
        Iterates over possible headings and returns the section text from the first successful extraction.
    """
    for heading in possible_headings:
        section_text = extract_section(text, heading)
        if section_text:
            return section_text
    return None

def filter_late_policy(text):
    """
    Input:
        text (str): Text that may include late submission policy details.
    Output:
        A string containing only lines that mention "late" or "penalty".
    Function description:
        Splits the text into lines, filters for lines with "late" or "penalty" (case-insensitive), and joins them.
    """
    lines = text.splitlines()
    filtered = [line.strip() for line in lines if "late" in line.lower() or "penalty" in line.lower()]
    return "\n".join(filtered)

@app.route('/extract', methods=['POST'])
def extract_pdf_sections():
    """
    REST API endpoint to extract sections from an uploaded PDF.
    Input (via POST request):
        file: The PDF file to be processed (multipart/form-data).
    Output:
        JSON object containing the extracted sections.
    Function description:
        Saves the uploaded PDF to a temporary location, processes it to extract text using pdfplumber or OCR,
        extracts the Late Policy, Grading Policy, and Grading Weights sections based on candidate headings and boundaries,
        and returns these sections as a JSON response.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected."}), 400

    upload_folder = 'uploads'
    os.makedirs(upload_folder, exist_ok=True)
    file_path = os.path.join(upload_folder, file.filename)
    file.save(file_path)

    pdf_text = extract_text_from_pdf(file_path)
    if not pdf_text.strip() or pdf_text.strip().startswith("%PDF"):
        pdf_text = extract_text_with_ocr(file_path)

    sections = {
        "Late Policy": {
            "headings": ["Homework:"],
            "filter": "late_policy"
        },
        "Grading Policy": {
            "headings": ["Grading Scale:", "Grading Scale"],
            "boundaries": ["Attendance", "Course Policies"]
        },
        "Grading Weights": {
            "headings": ["Grade Evaluation:", "Grade Evaluation", "Graded Work:", "Graded Work"],
            "boundaries": ["Grading Scale"]  # For the 341 syllabus: extract until Grading Scale.
        }
    }

    extracted_data = {}
    for section, params in sections.items():
        headings = params.get("headings", [])
        boundaries = params.get("boundaries", None)
        section_text = None
        if boundaries:
            for heading in headings:
                section_text = extract_section_with_boundaries(pdf_text, heading, boundaries)
                if section_text:
                    break
            if not section_text:
                section_text = extract_section_multiple(pdf_text, headings)
        else:
            section_text = extract_section_multiple(pdf_text, headings)
        
        if section == "Late Policy" and section_text:
            section_text = filter_late_policy(section_text)
        
        extracted_data[section] = section_text

    os.remove(file_path)
    return jsonify(extracted_data)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
