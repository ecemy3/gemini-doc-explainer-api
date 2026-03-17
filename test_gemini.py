from google import genai
from google.genai.types import GenerateContentConfig


def run_demo_request() -> None:
    client = genai.Client(
        vertexai=True,
        project="ai-doc-explainer",
        location="europe-west1",
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents="Explain what Cloud Run is in simple terms.",
        config=GenerateContentConfig(
            temperature=0.3,
            max_output_tokens=300,
        ),
    )

    print(response.text)


if __name__ == "__main__":
    run_demo_request()
