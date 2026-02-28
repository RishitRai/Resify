import asyncio
import os
import sys
from dotenv import load_dotenv

# Load variables from .env if present
load_dotenv()

from google import genai

async def test_gemini():
    print("=" * 60)
    print("🤖 Gemini API Tester")
    print("=" * 60)
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ Error: GEMINI_API_KEY is not set in the environment or .env file.")
        sys.exit(1)

    print("🔑 GEMINI_API_KEY found. Initializing client...")
    try:
        client = genai.Client(api_key=api_key)
        print("✅ Client initialized successfully.")

        model_name = os.getenv("LLM_MODEL", "gemma-3-27b-it")
        test_prompt = "Hello Gemini! This is a simple API test. Please respond with a short confirmation that you are operational."
        
        print(f"🚀 Sending test prompt to '{model_name}'...")
        print(f"Prompt: \"{test_prompt}\"")
        
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=test_prompt
        )

        print("-" * 60)
        print("📥 Received response:")
        print(response.text.strip())
        print("-" * 60)
        print("✨ SUCCESS: Gemini API is working properly!")

    except Exception as e:
        print("-" * 60)
        print("❌ FAILED: Error communicating with Gemini API:")
        import traceback
        traceback.print_exc()
        print("-" * 60)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test_gemini())
