import asyncio
import os
import uuid
import getpass
import pprint
import openai
import operator
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.prompts import ChatPromptTemplate
from langchain import hub
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.graph import END, StateGraph, START
from langsmith.wrappers import wrap_openai
from langsmith import traceable
from typing import Annotated, List, Tuple, Union
from typing_extensions import TypedDict
from pydantic import BaseModel, Field

os.environ["OPENAI_API_KEY"] = (
    "sk-?"
) if os.environ.get("OPENAI_API_KEY") is None else os.environ["OPENAI_API_KEY"]
os.environ["TAVILY_API_KEY"] = "tvly-?" if os.environ.get("TAVILY_API_KEY") is None else os.environ["TAVILY_API_KEY"]
os.environ["LANGCHAIN_TRACING_V2"] = "true" if os.environ.get("LANGCHAIN_TRACING_V2") is None else os.environ["LANGCHAIN_TRACING_V2"]
os.environ["LANGCHAIN_API_KEY"] = "lsv2_pt?" if os.environ.get("LANGCHAIN_API_KEY") is None else os.environ["LANGCHAIN_API_KEY"]

# Auto-trace LLM calls in-context
client = wrap_openai(openai.Client())


# Define the State
class PlanExecute(TypedDict):
    input: str
    plan: List[str]
    past_steps: Annotated[List[Tuple], operator.add]
    response: str


class Plan(BaseModel):
    """Plan to follow in future"""

    steps: List[str] = Field(
        description="different steps to follow, should be in sorted order"
    )


class Response(BaseModel):
    """Response to user."""

    response: str


class Act(BaseModel):
    """Action to perform."""

    action: Union[Response, Plan] = Field(
        description="Action to perform. If you want to respond to user, use Response. "
        "If you need to further use tools to get the answer, use Plan."
    )


def _set_env(var: str):
    if not os.environ.get(var):
        os.environ[var] = getpass.getpass(f"{var}: ")


def predict_react_agent_answer(example: dict):
    """Use this for answer evaluation"""

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    # messages = react_graph.invoke({"messages": ("user", example["input"])}, config)
    return {"response": "response"}


@traceable  # Auto-trace this function
def pipeline(user_input: str):
    result = client.chat.completions.create(
        messages=[{"role": "user", "content": user_input}], model="gpt-4o-mini"
    )
    return result.choices[0].message.content


async def planner_demo():
    # tavily_tool = TavilySearchResults(max_results=3)
    planner_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """For the given objective, come up with a simple step by step plan. \
    This plan should involve individual tasks, that if executed correctly will yield the correct answer. Do not add any superfluous steps. \
    The result of the final step should be the final answer. Make sure that each step has all the information needed - do not skip steps.""",
            ),
            ("placeholder", "{messages}"),
        ]
    )
    planner = planner_prompt | ChatOpenAI(
        model="gpt-4o", temperature=0
    ).with_structured_output(Plan)
    # TODO: update the plan
    planner.invoke(
        {
            "messages": [
                (
                    "user",
                    "generate a C++ fuzzing harness for function `int xmlTextReaderSetSchema(xmlTextReaderPtr reader, xmlSchemaPtr schema)` from project `libxml2`",
                )
            ]
        }
    )

    # =================================================================================================
    # replanner
    replanner_prompt = ChatPromptTemplate.from_template(
        """For the given objective, come up with a simple step by step plan. \
This plan should involve individual tasks, that if executed correctly will yield the correct answer. Do not add any superfluous steps. \
The result of the final step should be the final answer. Make sure that each step has all the information needed - do not skip steps.

Your objective was this:
{input}

Your original plan was this:
{plan}

You have currently done the follow steps:
{past_steps}

Update your plan accordingly. If no more steps are needed and you can return to the user, then respond with that. Otherwise, fill out the plan. Only add steps to the plan that still NEED to be done. Do not return previously done steps as part of the plan."""
    )
    replanner = replanner_prompt | ChatOpenAI(
        model="gpt-4o", temperature=0
    ).with_structured_output(Act)

    # Get the prompt to use - you can modify this!
    prompt = hub.pull("ih/ih-react-agent-executor")
    prompt.pretty_print()
    tools = [TavilySearchResults(max_results=3)]

    # Choose the LLM that will drive the agent
    llm = ChatOpenAI(model="gpt-4o")
    agent_executor = create_react_agent(llm, tools, state_modifier=prompt)

    async def execute_step(state: PlanExecute):
        plan = state["plan"]
        plan_str = "\n".join(f"{i+1}. {step}" for i, step in enumerate(plan))
        task = plan[0]
        task_formatted = f"""For the following plan:
    {plan_str}\n\nYou are tasked with executing step {1}, {task}."""
        agent_response = await agent_executor.ainvoke(
            {"messages": [("user", task_formatted)]}
        )
        return {
            "past_steps": [(task, agent_response["messages"][-1].content)],
        }

    async def plan_step(state: PlanExecute):
        plan = await planner.ainvoke({"messages": [("user", state["input"])]})
        return {"plan": plan.steps}

    async def replan_step(state: PlanExecute):
        output = await replanner.ainvoke(state)
        if isinstance(output.action, Response):
            return {"response": output.action.response}
        else:
            return {"plan": output.action.steps}

    def should_end(state: PlanExecute):
        if "response" in state and state["response"]:
            return END
        else:
            return "agent"

    workflow = StateGraph(PlanExecute)

    # Add the plan node
    workflow.add_node("planner", plan_step)

    # Add the execution step
    workflow.add_node("agent", execute_step)

    # Add a replan node
    workflow.add_node("replan", replan_step)

    workflow.add_edge(START, "planner")

    # From plan we go to agent
    workflow.add_edge("planner", "agent")

    # From agent, we replan
    workflow.add_edge("agent", "replan")

    workflow.add_conditional_edges(
        "replan",
        # Next, we pass in the function that will determine which node is called next.
        should_end,
        ["agent", END],
    )

    # Finally, we compile it!
    # This compiles it into a LangChain Runnable,
    # meaning you can use it as you would any other runnable
    app = workflow.compile()
    print(app.get_graph().draw_mermaid())

    config = {"recursion_limit": 50}
    inputs = {
        "input": """
Generate a C++ fuzzing harness that initializes all parameters and calls the function.
The target function is `int xmlTextReaderSetSchema(xmlTextReaderPtr reader, xmlSchemaPtr schema)` from project `libxml2`.
Guidelines:
 - Carefully study the function signature and initialize all parameters.
 - Use FuzzedDataProvider for generating various input data types.
 - Ensure the code compiles successfully, including any required header files.
 - All variables must be declared and initialized before usage.
 - Avoid creating new variables with names identical to existing ones.
 - Add type casts where necessary to ensure type matching.
 - Avoid using random number generators such as rand().
 - If using `goto`, declare variables before the `goto` label.
 - You must call target function in the solution.
 - The fuzzing harness should be compatible with libfuzzer.
        """
    }
    async for event in app.astream(inputs, config=config):
        for k, v in event.items():
            if k != "__end__":
                pprint.pprint(v)


async def main():
    _set_env("OPENAI_API_KEY")
    _set_env("TAVILY_API_KEY")

    # Example of a user input
    user_input = (
        "Task: generate fuzzing harness based on the function signature and guidelines."
    )
    print(user_input)
    # print(pipeline(user_input))
    await planner_demo()


asyncio.run(main())
