from crewai import Crew, Agent, Task
from langchain_litellm import ChatLiteLLM
import json

from utils.helper import (
    get_crew_output,
    ensure_dict,
    extract_json_block
)


# LLM Setup
llm = ChatLiteLLM(
    model="ollama/llama3",
    base_url="http://localhost:11434",
    temperature=0.7
)


# Manager Agent
manager = Agent(
    role="Document Manager",
    goal="Determine the type of document and assign extraction tasks accordingly",
    backstory="A strategic thinker who understands all types of business documents and routes them to the right workflows.",
    llm=llm,
    verbose=True
)


# Supervisor Agent
validation_supervisor = Agent(
    role="Bill Validation Supervisor",
    goal="Review the extracted bill fields and verify they are complete, valid, and logically consistent.",
    backstory="A quality control specialist who ensures all bill data is clean before sending it downstream to finance.",
    llm=llm,
    verbose=True
)


# Performer Agent
bill_field_extractor = Agent(
    role="Bill Field Extractor",
    goal="Extract structured bill fields from OCR text of a bill",
    backstory="An experienced assistant trained to read scanned bills, identify key financial fields, and structure them cleanly.",
    llm=llm,
    verbose=True
)


# TASK: Document Classification
task_classify = Task(
    description="""
Given the following OCR text, determine the type of document it is based on the sender and content.

Use the following rules to classify the document:

- **Bill**: An invoice received from a vendor. It usually shows a vendor name, invoice number, due date, and total amount due. It may include terms like "Remit To", "Amount Due", "Vendor", or "Payable To". "Rogers Build" is the company the bill is sent to, sold to, or delivered to.
- **Invoice**: An invoice sent to a customer. It often includes the business’s name at the top, and may say “Invoice to”, “Bill To”, or “Client”.
- **Receipt**: A payment confirmation from a credit card or bank account.
- **Purchase Order**: A document issued to a vendor with PO number and order details.
- **Credit Memo**: A vendor-issued credit note, often showing negative values or phrases like “Credit Memo” or “Credit issued”.
- **Quote** or **Bid**: A non-final pricing offer, not yet approved or billed.
- **Unknown**: Use this if none of the above apply.

OCR TEXT:
{ocr_text}

Instructions:
1. Use clear logic based on who the sender is and what the document shows.
2. If it looks like it's from a **vendor**, classify it as a **Bill**.
3. If it looks like it's from your own company to a customer, classify it as an **Invoice**.
4. Respond with a **single line JSON object** only. Do not include explanations.

Format:
{{ "document_type": "Bill" }}
""",
    expected_output="A JSON object containing the document_type field.",
    agent=manager
)


# TASK: Field Extraction
task_extract_bill_fields = Task(
    description="""
You are given OCR text from a scanned bill.

OCR TEXT:
{ocr_text}

Extract the following fields from the OCR and return them in valid JSON format:

- vendor: Name of the vendor
- invoice_number: The bill number
- invoice_date: Date of the bill (in MM/DD/YYYY format)
- total_amount: Total amount due (in "$1234.56 format)

Respond in this format:
{{
  "vendor": "...",
  "invoice_number": "...",
  "invoice_date": "MM/DD/YYYY",
  "total_amount": "$..."
}}
""",
    expected_output="A valid JSON object with the extracted bill fields.",
    agent=bill_field_extractor
)


task_validate_bill_fields = Task(
    description="""
You are given structured bill data extracted from OCR text.

Please check the following:
- Are all fields present (vendor, invoice_number, invoice_date, total_amount)?
- Are the dates in MM/DD/YYYY format?
- Is the total amount greater than zero and formatted like \"$123.45\"?
- Is there anything that appears obviously incorrect?

INVOICE DATA:
{extracted_fields}

Respond in this format:
{{
  "validated": true,
  "issues": []
}}

If there are problems:
{{
  "validated": false,
  "issues": ["Date formats are not valid", "Total amount is not greater than $0", "Missing fields", "Vendor name and invoice number are not present"]
}}
""",
    expected_output="Validation result and list of issues",
    agent=validation_supervisor
)




# RUN CREW
def run_crew(ocr_text: str):

    # CREW SETUP: Manager, Supervisor, Performer
    classification_crew = Crew(
        agents=[manager],
        tasks=[task_classify],
        manager_agent=manager,
        verbose=True,
    )
    classification_result = classification_crew.kickoff(
        inputs={"ocr_text": ocr_text}
    )
    #print("DEBUG: Raw classification result: ", classification_result)
    #print("DEBUG: Tasks output: ", classification_result.tasks_output)
    #print("DEBUG: Tasks output output: ", classification_result.tasks_output[0].output)
    #print("DEBUG: dir(classification_result): ", dir(classification_result))
    #classification_result_output = get_crew_output(
    #    result=classification_result
    #)
    #print(classification_result_output)
    document_type = json.loads(
        classification_result.tasks_output[0].raw
    ).get("document_type")
    print("DEBUG: Document type: ", document_type)

    if document_type == "Bill":
        # Now extract bill fields
        extraction_crew = Crew(
            agents=[bill_field_extractor],
            tasks=[task_extract_bill_fields],
            manager_agent=bill_field_extractor,
            verbose=True
        )
        extraction_result = extraction_crew.kickoff(
            inputs={"ocr_text": ocr_text}
        )
        #print(extraction_result)
        extracted_fields = json.loads(
            extraction_result.tasks_output[0].raw
        )
        print("DEBUG: Extracted fields: ", extracted_fields)

        # Now validate with the Supervisor
        validation_crew = Crew(
            agents=[validation_supervisor],
            tasks=[task_validate_bill_fields],
            manager_agent=validation_supervisor,
            verbose=True
        )
        validation_result = validation_crew.kickoff(
            inputs={"extracted_fields": extracted_fields}
        )
        validation_fields = json.loads(
            validation_result.tasks_output[0].raw
        )
        print("DEBUG: Validation fields: ", validation_fields)


    '''
    return {
        "document_type": doc_type,
        "extracted": extracted_fields,
        "validation": validation_data
    }
    '''
    return "Process completed"
