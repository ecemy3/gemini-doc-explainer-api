from google import genai
from google.genai.types import GenerateContentConfig

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
