"""在终端里和客服 Agent 聊天（测试用）

用法（在 backend 资料夹里）：
    python chat.py
输入 exit 离开。
"""

from agent.agent import MODEL, CustomerServiceAgent


def main():
    agent = CustomerServiceAgent()
    print(f"Glow Atelier 光研美学 客服（模型：{MODEL}）。输入 exit 离开。\n")

    while True:
        message = input("你：").strip()
        if message.lower() in {"exit", "quit"}:
            break
        if not message:
            continue

        result = agent.chat(message)

        for step in result["trace"]:
            print(f"   🔍 {step['tool']}({step['args'].get('query', '')}) → {', '.join(step['results'][:3])}")
        print(f"\n客服：{result['answer']}")
        print(f"   （tokens：输入 {result['tokens']['input']}，输出 {result['tokens']['output']}）\n")


if __name__ == "__main__":
    main()
