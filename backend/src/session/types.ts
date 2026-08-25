import type { JsonObject, JsonValue } from "../models/json.js";
import type { AssistantMessage, UserMessage, ToolResultMessage } from "../models/messages.js";

export type SessionMetadata = Record<string, JsonValue>;

export type SessionHeaderEntry = {
	readonly kind: "session";
	readonly sequence: 0;
	readonly sessionId: string;
	readonly projectId: string;
	readonly cwd: string;
	readonly createdAt: string;
	readonly metadata: SessionMetadata;
};

export type SessionMessageEntry = {
	readonly kind: "message";
	readonly sequence: number;
	readonly timestamp: number;
	readonly message: UserMessage | AssistantMessage;
};

export type SessionToolResultEntry = {
	readonly kind: "tool_result";
	readonly sequence: number;
	readonly timestamp: number;
	readonly message: ToolResultMessage<unknown>;
};

export type SessionBranchEntry = {
	readonly kind: "branch";
	readonly sequence: number;
	readonly timestamp: number;
	readonly parentSessionId: string;
	readonly parentEntryCount: number;
};

export type SessionMetadataEntry = {
	readonly kind: "metadata";
	readonly sequence: number;
	readonly timestamp: number;
	readonly patch: JsonObject;
};

export type SessionCustomEntry = {
	readonly kind: "custom";
	readonly sequence: number;
	readonly timestamp: number;
	readonly namespace: string;
	readonly type: string;
	readonly payload: JsonObject;
};

export type SessionEntry =
	| SessionHeaderEntry
	| SessionMessageEntry
	| SessionToolResultEntry
	| SessionBranchEntry
	| SessionMetadataEntry
	| SessionCustomEntry;

export type SessionAppendEntry =
	| Omit<SessionMessageEntry, "sequence">
	| Omit<SessionToolResultEntry, "sequence">
	| Omit<SessionBranchEntry, "sequence">
	| Omit<SessionMetadataEntry, "sequence">
	| Omit<SessionCustomEntry, "sequence">;
