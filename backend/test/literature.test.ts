import assert from "node:assert/strict";
import { test } from "node:test";
import { OpenAlexLiteratureProvider } from "../src/research/literature.js";

for (const mediaType of ["text/plain", "text/html", "application/xhtml+xml"]) {
	for (const text of ["a".repeat(10_001), "汉".repeat(4_000), "😀".repeat(3_000), "a" + "😀".repeat(3_000)]) {
		test(`literature byte budget preserves UTF-8 for ${mediaType}, prefix ${[...text].slice(0, 2).join("")}`, async (t) => {
			t.mock.method(globalThis, "fetch", async () => new Response(text, { headers: { "content-type": mediaType } }));
			const result = await new OpenAlexLiteratureProvider({ maxBytes: 10_000 }).acquire({ sourceId: "fixture", title: "Fixture", authors: [], acquisitionUrl: "https://fixture.invalid" });
			assert.ok(result);
			assert.ok(Buffer.byteLength(result.body) <= 10_000);
			assert.ok(Buffer.byteLength(result.body) >= 9_997);
			assert.ok(text.startsWith(result.body));
			assert.ok(!result.body.includes("\uFFFD"));
		});
	}
}

test("literature acquisition preserves text below the byte limit", async (t) => {
	const text = "Short source: 汉字 😀";
	t.mock.method(globalThis, "fetch", async () => new Response(text, { headers: { "content-type": "text/plain" } }));
	const result = await new OpenAlexLiteratureProvider({ maxBytes: 10_000 }).acquire({ sourceId: "fixture", title: "Fixture", authors: [], acquisitionUrl: "https://fixture.invalid" });
	assert.equal(result?.body, text);
});
