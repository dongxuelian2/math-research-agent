import { readdir, readFile, unlink, mkdir, writeFile } from "node:fs/promises";
import { extname, relative, resolve, sep } from "node:path";
import type { ProofItemInput, ProofRepositoryItem } from "./types.js";

const ITEM_EXTENSIONS = [".md", ".txt", ".lean"] as const;

export class ProofRepository {
	readonly root: string;

	constructor(root: string) {
		this.root = resolve(root);
	}

	async ensure(): Promise<void> {
		await mkdir(this.root, { recursive: true });
	}

	async listItems(): Promise<ProofRepositoryItem[]> {
		await this.ensure();
		const files = await collectFiles(this.root);
		const items: ProofRepositoryItem[] = [];
		for (const file of files.sort()) {
			const extension = extname(file);
			if (!ITEM_EXTENSIONS.includes(extension as (typeof ITEM_EXTENSIONS)[number])) {
				continue;
			}
			const content = await readFile(file, "utf8");
			items.push({
				slug: relative(this.root, file).slice(0, -extension.length).split(sep).join("/"),
				summary: summaryOf(content),
				content,
				format: extension === ".lean" ? "lean" : "text",
			});
		}
		return items;
	}

	async listSummaries(): Promise<readonly ProofRepositoryItem[]> {
		return this.listItems();
	}

	/** OpenProver's planner-facing one-line index: [[slug]]: summary. */
	async formatIndex(): Promise<string> {
		const items = await this.listItems();
		return items
			.map((item) => `- [[${item.slug}]]: ${item.summary}`)
			.join("\n");
	}

	async findByContent(content: string): Promise<ProofRepositoryItem[]> {
		const normalized = content.trim();
		return (await this.listItems()).filter((item) => item.content.trim() === normalized);
	}

	async readItem(slug: string): Promise<ProofRepositoryItem | undefined> {
		const safeSlug = validateSlug(slug);
		for (const extension of ITEM_EXTENSIONS) {
			const file = resolve(this.root, `${safeSlug}${extension}`);
			try {
				const content = await readFile(file, "utf8");
				return {
					slug: safeSlug,
					summary: summaryOf(content),
					content,
					format: extension === ".lean" ? "lean" : "text",
				};
			} catch (error) {
				if (!isMissingFile(error)) {
					throw error;
				}
			}
		}
		return undefined;
	}

	async readItems(slugs: readonly string[]): Promise<string> {
		const sections: string[] = [];
		for (const slug of slugs) {
			const item = await this.readItem(slug);
			sections.push(item === undefined ? `[[${slug}]]\nNOT FOUND` : `[[${item.slug}]]\n${item.content}`);
		}
		return sections.join("\n\n");
	}

	async writeItem(item: ProofItemInput): Promise<ProofRepositoryItem | undefined> {
		const safeSlug = validateSlug(item.slug);
		if (item.content === undefined) {
			await Promise.all(
				ITEM_EXTENSIONS.map(async (extension) => {
					try {
						await unlink(resolve(this.root, `${safeSlug}${extension}`));
					} catch (error) {
						if (!isMissingFile(error)) {
							throw error;
						}
					}
				}),
			);
			return undefined;
		}
		const extension = item.format === "lean" ? ".lean" : ".md";
		const file = resolve(this.root, `${safeSlug}${extension}`);
		await mkdir(resolve(file, ".."), { recursive: true });
		await writeFile(file, item.content, "utf8");
		const otherExtension = extension === ".md" ? ".lean" : ".md";
		try {
			await unlink(resolve(this.root, `${safeSlug}${otherExtension}`));
		} catch (error) {
			if (!isMissingFile(error)) {
				throw error;
			}
		}
		return {
			slug: safeSlug,
			summary: item.summary ?? summaryOf(item.content),
			content: item.content,
			format: item.format ?? "text",
		};
	}

	async writeItems(items: readonly ProofItemInput[]): Promise<void> {
		for (const item of items) {
			await this.writeItem(item);
		}
	}

	async resolveWikilinks(text: string): Promise<string> {
		const slugs = [...text.matchAll(/\[\[([^\]]+)\]\]/g)].map((match) => match[1]?.trim() ?? "");
		const uniqueSlugs = [...new Set(slugs.filter((slug) => slug.length > 0))];
		if (uniqueSlugs.length === 0) {
			return text;
		}
		const materials = await this.readItems(uniqueSlugs);
		return `${text}\n\n## Referenced Materials\n\n${materials}`;
	}
}

async function collectFiles(directory: string): Promise<string[]> {
	const entries = await readdir(directory, { withFileTypes: true });
	const files: string[] = [];
	for (const entry of entries) {
		const path = resolve(directory, entry.name);
		if (entry.isDirectory()) {
			files.push(...(await collectFiles(path)));
		} else if (entry.isFile()) {
			files.push(path);
		}
	}
	return files;
}

function validateSlug(slug: string): string {
	const normalized = slug.trim().replaceAll("\\", "/");
	if (
		normalized.length === 0 ||
		normalized.length > 100 ||
		normalized.startsWith("/") ||
		normalized.includes("\0") ||
		normalized.split("/").some((part) => part === ".." || part === "." || part.length === 0)
	) {
		throw new Error(`Invalid proof repository slug: ${slug}`);
	}
	return normalized;
}

function summaryOf(content: string): string {
	const first = content
		.split(/\r?\n/)
		.map((line) => line.trim())
		.find((line) => line.length > 0);
	return (first ?? "(empty item)").replace(/^#+\s*/, "").slice(0, 200);
}

function isMissingFile(error: unknown): boolean {
	return typeof error === "object" && error !== null && "code" in error && error.code === "ENOENT";
}
