export type SseEvent = {
	readonly event: string;
	readonly data: string;
};

export async function* parseSse(chunks: AsyncIterable<string>): AsyncIterable<SseEvent> {
	let buffer = "";
	let eventName = "message";
	let dataLines: string[] = [];

	const flush = (): SseEvent | undefined => {
		if (dataLines.length === 0) {
			return undefined;
		}

		const event: SseEvent = { event: eventName, data: dataLines.join("\n") };
		eventName = "message";
		dataLines = [];
		return event;
	};

	for await (const chunk of chunks) {
		buffer += chunk;
		const lines = buffer.split(/\n/);
		buffer = lines.pop() ?? "";

		for (const rawLine of lines) {
			const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
			if (line.length === 0) {
				const event = flush();
				if (event !== undefined) {
					yield event;
				}
				continue;
			}
			if (line.startsWith(":")) {
				continue;
			}
			if (line.startsWith("event:")) {
				eventName = line.slice("event:".length).trim();
				continue;
			}
			if (line.startsWith("data:")) {
				dataLines.push(line.slice("data:".length).trimStart());
			}
		}
	}

	if (buffer.length > 0) {
		const line = buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer;
		if (line.startsWith("data:")) {
			dataLines.push(line.slice("data:".length).trimStart());
		}
	}

	const event = flush();
	if (event !== undefined) {
		yield event;
	}
}
