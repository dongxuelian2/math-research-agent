import { AsyncLocalStorage } from "node:async_hooks";

export interface ProofToolScope { readonly role: "planner" | "worker" | "verifier"; readonly logicalTaskId?: string; }
const storage = new AsyncLocalStorage<ProofToolScope>();

export function withProofToolScope<T>(scope: ProofToolScope, operation: () => Promise<T>): Promise<T> {
	return storage.run(scope, operation);
}

export function currentProofToolScope(): ProofToolScope | undefined { return storage.getStore(); }
