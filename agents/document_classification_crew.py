from crewai import Crew, Agent, Task
from agents.llm import OllamaModel
import json

from utils.helper import (
    get_crew_output,
    ensure_dict,
    extract_json_block
)


# LLM Setup
def get_llm_model(model: str) -> OllamaModel:
    return OllamaModel(
        model=model
    )


def get_crew_agent(llm: OllamaModel) -> Agent:
    return Agent(
        role="Document Classification Agent",
        goal="Determine the type of document and assign extraction tasks accordingly",
        backstory="A strategic thinker who understands all types of business documents and routes them to the right workflows.",
        llm=llm,
        verbose=True
    )


def get_agent_task(text: str, agent: Agent) -> Task:
    return Task(
        agent=agent,
        expected_output="A JSON object containing the document_type field.",
        description="""
You are a document classification expert.

Classify the following document based only on its text content.

Choose one of the following document types:
- Bill
- Invoice
- Receipt
- Credit Memo
- Purchase Order
- Quote
- Unknown

Use the following rules:
- A **Bill** is a request for payment from a vendor to your company. It usually includes terms like "Remit To", "Amount Due", "Due Date", "Payable", or "Vendor".
- A **Receipt** confirms payment already made. It may show "Paid", "Payment Method", "Card", or "Transaction ID".
- An **Invoice** is something your company sent to a customer. It might include terms like "Bill To", "Client", or your company name as the sender.

Return your answer as a valid JSON object with this exact format:
{{
  "document_type": "..."
}}

Do not include explanations or any other text. Only return the JSON object.

DOCUMENT TEXT:
{text}
    """
    )


def get_agent_crew(agent: Agent, task: Task) -> Crew:
    return Crew(
        agents=[agent],
        tasks=[task],
        verbose=True,
    )


# RUN CREW
def run_crew(ocr_text: str):

    # Check if OCR text is not None
    if ocr_text is not None:
        print("✅ Step 1: Received OCR text")

    # Get LLM models
    try:
        llm_gemma3 = get_llm_model("ollama/gemma3:4b")
        llm_llama32 = get_llm_model("ollama/llama3.2")
        print("✅ Step 2: LLM models loaded")
    except Exception as e:
        print(f"❌ Error getting LLM models: {e}")
        return None

    # Get crew agent
    try:
        agent = get_crew_agent(llm_llama32)
        print("✅ Step 3: Crew agent loaded")
    except Exception as e:
        print(f"❌ Error getting crew agent: {e}")
        return None

    # Get agent task
    try:
        task = get_agent_task(ocr_text, agent)
        print("✅ Step 4: Agent task loaded")
    except Exception as e:
        print(f"❌ Error getting agent task: {e}")
        return None    

    # Get crew
    try:
        crew = get_agent_crew(agent, task)
        print("✅ Step 5: Crew created")
    except Exception as e:
        print(f"❌ Error getting crew: {e}")
        return None

    # Kick off crew
    try:
        output = crew.kickoff()
        print("✅ Step 6: Crew kicked off")
        print("DEBUG: Output: ", output)
    except Exception as e:
        print(f"❌ Error kicking off crew: {e}")
        return None

    # Extract document type
    document_type = extract_json_block(
        output.tasks_output[0].raw
    ).get("document_type")

    print("DEBUG: Document type: ", document_type)

    return {
        "document_type": document_type
    }
