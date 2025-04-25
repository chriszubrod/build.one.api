import os
from crewai import Crew, Agent, Task
from langchain_ollama import OllamaLLM
from langchain_litellm import ChatLiteLLM
from litellm import completion

#os.environ["OPENAI_API_KEY"] = "sk-proj-1234567890"



def run_agent():
    llm = ChatLiteLLM(
        model="ollama/gemma3:4b",
        base_url="http://localhost:11434",
        temperature=0.7
    )

    agent = Agent(
        role="Friendly Greeter",
        goal="Welcome the user to this Flask + CrewAI app",
        backstory="An AI who loves to say hello and help users feel welcome.",
        verbose=True,
        llm=llm
    )

    task = Task(
        description="Say hello to the visitor",
        expected_output="A friendly greeting with a smile :)",
        agent=agent
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True
    )

    result = crew.kickoff()
    return result
