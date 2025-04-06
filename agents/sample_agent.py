from crewai import Agent, Task, Crew

def run_agent():
    agent = Agent(
        role="Friendly Greeter",
        goal="Welcome the user to this Flask + CrewAI app",
        backstory="An AI who loves to say hello and help users feel welcome.",
        verbose=True
    )

    task = Task(
        description="Say hello to the visitor",
        expected_output="A friendly greeting with a smile :)",
        agent=agent
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
    )

    result = crew.kickoff()
    return result
