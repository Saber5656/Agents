# summarize-tool-releases Targets

Primary URL を優先する。
RSS / Atom が安定している場合は RSS / Atom を Primary にする。
Fallback は Primary 取得失敗時、または Sparse release enrichment に使う。

## Category Slugs

| category | category-slug |
|---|---|
| Frontend | `frontend` |
| Language | `language` |
| Runtime | `runtime` |
| DevTools | `devtools` |
| Backend | `backend` |
| RDBMS | `rdbms` |
| NoSQL | `nosql` |
| Auth | `auth` |
| Cloud | `cloud` |
| AI API | `ai-api` |
| AI Agent | `ai-agent` |
| Editor | `editor` |

## Frontend

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| react | React | Framework | https://github.com/facebook/react/releases.atom | https://api.github.com/repos/facebook/react/releases?per_page=10 |
| nextjs | Next.js | Framework | https://github.com/vercel/next.js/releases.atom | https://api.github.com/repos/vercel/next.js/releases?per_page=10 |
| vue | Vue | Framework | https://github.com/vuejs/core/releases.atom | https://api.github.com/repos/vuejs/core/releases?per_page=10 |
| nuxt | Nuxt | Framework | https://github.com/nuxt/nuxt/releases.atom | https://api.github.com/repos/nuxt/nuxt/releases?per_page=10 |
| svelte | Svelte | Framework | https://github.com/sveltejs/svelte/releases.atom | https://api.github.com/repos/sveltejs/svelte/releases?per_page=10 |

## Language / Runtime

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| typescript | TypeScript | Language | https://github.com/microsoft/TypeScript/releases.atom | https://devblogs.microsoft.com/typescript/feed/ |
| php | PHP | Language | https://www.php.net/releases/feed.php | https://github.com/php/php-src/releases.atom |
| go | Go | Language | https://github.com/golang/go/releases.atom | https://go.dev/blog/index.xml |
| nodejs | Node.js | Runtime | https://nodejs.org/en/feed/releases.xml | https://github.com/nodejs/node/releases.atom |

## DevTools

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| vite | Vite | Bundler | https://github.com/vitejs/vite/releases.atom | https://api.github.com/repos/vitejs/vite/releases?per_page=10 |
| docker | Docker | Container | https://github.com/docker/docker-ce/releases.atom | https://docs.docker.com/engine/release-notes/ |
| kubernetes | Kubernetes | Orchestration | https://github.com/kubernetes/kubernetes/releases.atom | https://kubernetes.io/feed.xml |
| github | GitHub | VCS Platform | https://github.blog/changelog/feed/ | https://github.blog/changelog/ |

## Backend / Auth

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| postgres | PostgreSQL | RDBMS | https://www.postgresql.org/news.atom | https://github.com/postgres/postgres/tags.atom |
| prisma | Prisma | ORM | https://github.com/prisma/prisma/releases.atom | https://api.github.com/repos/prisma/prisma/releases?per_page=10 |

## Cloud

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| gcp | Google Cloud | Hyperscaler | https://cloud.google.com/feeds/gcp-release-notes.xml | https://cloud.google.com/release-notes |
| aws | AWS | Hyperscaler | https://aws.amazon.com/about-aws/whats-new/recent/feed/ | https://aws.amazon.com/new/ |
| azure | Azure | Hyperscaler | https://www.microsoft.com/releasecommunications/api/v2/azure/rss | https://azure.microsoft.com/en-us/updates/ |
| vercel | Vercel | Hosting | https://vercel.com/changelog/feed.xml | https://vercel.com/changelog |
| supabase | Supabase | BaaS | https://github.com/supabase/supabase/releases.atom | https://github.com/orgs/supabase/discussions/categories/changelog |
| cloudflare | Cloudflare | Edge Platform | https://developers.cloudflare.com/changelog/index.xml | https://blog.cloudflare.com/rss/ |

## AI

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| openai-api | OpenAI API | LLM Platform | https://developers.openai.com/changelog/rss.xml | https://platform.openai.com/docs/changelog |
| claude | Claude | LLM Platform | https://platform.claude.com/docs/en/release-notes/overview | https://support.claude.com/en/articles/12138966-release-notes |
| gemini | Gemini API | LLM Platform | https://ai.google.dev/gemini-api/docs/changelog | https://blog.google/products/gemini/ |
| codex | OpenAI Codex CLI | Agent CLI | https://developers.openai.com/codex/changelog | https://github.com/openai/codex/releases.atom |
| cursor | Cursor | AI IDE | https://cursor.com/changelog | https://changelog.cursor.com/feed |
| github-copilot | GitHub Copilot | AI IDE Plugin | https://github.blog/changelog/label/copilot/feed/ | https://github.blog/changelog/label/copilot/ |
| devin | Devin | Autonomous Agent | https://docs.devin.ai/changelog | https://devin.ai/ |

Codex は sparse prerelease が多いため、GitHub compare / PR / commit / changed files を fallback として必ず確認する。

## Editor

| slug | 表示名 | subcategory | Primary URL | Fallback URL |
|---|---|---|---|---|
| obsidian | Obsidian | Note App | https://obsidian.md/changelog/ | https://github.com/obsidianmd/obsidian-releases/releases.atom |
| vscode | VS Code | IDE | https://github.com/microsoft/vscode/releases.atom | https://code.visualstudio.com/updates |
