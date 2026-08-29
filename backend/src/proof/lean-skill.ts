import { readFile } from "node:fs/promises";
import { join } from "node:path";

/** Public source of the skill shipped under backend/src/proof/skills/lean4. */
export const LEAN4_SKILL_SOURCE = "https://github.com/cameronfreer/lean4-skills/tree/74febda7679a858af666903756a191f7a0437482/plugins/lean4/skills/lean4";

const SKILL_RELATIVE_PATH = join("backend", "src", "proof", "skills", "lean4", "SKILL.md");

/**
 * Load the vendored upstream skill at runtime. The TypeScript build does not
 * copy Markdown assets into dist/, so the repository path is intentional and
 * is also what is present in the Cloud Run image.
 */
export async function loadLean4Skill(repositoryDirectory: string): Promise<string | undefined> {
	try {
		return await readFile(join(repositoryDirectory, SKILL_RELATIVE_PATH), "utf8");
	} catch (error) {
		if ((error as NodeJS.ErrnoException | undefined)?.code === "ENOENT") return undefined;
		throw error;
	}
}
