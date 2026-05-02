import asyncio
import os

from dotenv import load_dotenv

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import AnthropicFoundryClient

load_dotenv()

MCP_URL = "http://127.0.0.1:8000/mcp/"


async def main() -> None:
    chat_client = AnthropicFoundryClient(
        model=os.environ["FOUNDRY_MODEL_DEPLOYMENT"],
        api_key=os.environ["FOUNDRY_API_KEY"],
        base_url=os.environ["FOUNDRY_ENDPOINT"],
    )

    mcp_tool = MCPStreamableHTTPTool(name="cupcake-store", url=MCP_URL)
    await mcp_tool.connect()

    try:
        instructions = await mcp_tool.get_prompt("agent_instructions")
        banner = await mcp_tool.get_prompt("welcome_banner")

        agent = Agent(
            client=chat_client,
            name="cupcake-agent",
            instructions=instructions,
            tools=mcp_tool,
        )
        session = agent.create_session()

        print(banner)
        print("Type 'exit' to quit.\n")

        # Kick things off automatically.
        reply = await agent.run("hello", session=session)
        print(f"\033[33mAssistant:\033[0m\n{reply.text}\n")

        while True:
            user_input = input("\033[33mYou:\033[0m\n").strip()
            if user_input.lower() in ("exit", "quit"):
                break
            if not user_input:
                continue
            reply = await agent.run(user_input, session=session)
            print(f"\033[33mAssistant:\033[0m\n{reply.text}\n")
    finally:
        await mcp_tool.close()


if __name__ == "__main__":
    asyncio.run(main())
