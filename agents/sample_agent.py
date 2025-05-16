import os
from crewai import Agent, Task, Process, Crew
from langchain_ollama import OllamaLLM
from langchain_litellm import ChatLiteLLM
from litellm import completion
from agents.llm import OllamaModel


os.environ["OPENAI_API_KEY"] = "sk-proj-1234567890"

llm = OllamaModel(
        model="ollama/llama3.2"
    )

marketer = Agent(
    role="Market Research Analyst",
    goal="Analyze the market and provide a report",
    backstory="An AI who loves to analyze the market and provide a report.",
    verbose=True,
    allow_delegation=False,
    llm=llm
)

technologist = Agent(
    role="Technology Expert",
    goal="Make assessment on how technologically feasible the company is and what type of technology they should use.",
    backstory="You are a technology expert who loves to make assessments on how technologically feasible the company is and what type of technology they should use.",
    verbose=True,
    allow_delegation=True,
    llm=llm
)

business_consultant = Agent(
    role="Business Development Consultant",
    goal="Evaluate and advise on the business model, scalability, and potential revenue streams.",
    backstory="You are a business development consultant who loves to evaluate and advise on the business model, scalability, and potential revenue streams.",
    verbose=True,
    allow_delegation=True,
    llm=llm
)

task_1 = Task(
    description="""Analyze the market demand for plugs for holes in crocs (shoes) so that the iconic footware looks less like swiss cheese.
    What a detailed report would include: market size, target audience, potential revenue, and a list of potential customers.""",
    agent=marketer,
    expected_output="A detailed report on the market demand for plugs for holes in crocs (shoes) so that the iconic footware looks less like swiss cheese."
)

task_2 = Task(
    description="""Analyze hoe to produce plugs for holes in crocs (shoes) so that the iconic footware looks less like swiss cheese.
    What a detailed report would include: market size, target audience, potential revenue, and a list of potential customers.""",
    agent=technologist,
    expected_output="A detailed report on how to produce plugs for holes in crocs (shoes) so that the iconic footware looks less like swiss cheese."
)

task_3 = Task(
    description="""Analyze and summarize marketing and technological report and write an detailed business plan with
    description of the business, target customers, revenue streams, and a plan for the next 3 years.""",
    agent=business_consultant,
    expected_output="A detailed business plan with description of the business, target customers, revenue streams, and a plan for the next 3 years."
)

crew = Crew(
    agents=[marketer, technologist, business_consultant],
    tasks=[task_1, task_2, task_3],
    process=Process.sequential,
    verbose=True
)

result = crew.kickoff()
print("###############################")
print(result)
print("###############################")

