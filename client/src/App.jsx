import { useMemo, useState } from 'react'
import './App.css'

const starterQuestions = [
  'Why did Arun return to the village and help?',
  'What is the moral lesson of the story?',
  'How did the villagers respond during the storm?',
]

function App() {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content:
        'I am ready to answer questions from the grounded document. Ask me about the story, the characters, or the key lessons.',
      sources: [],
    },
  ])
  const [loading, setLoading] = useState(false)

  const stats = useMemo(
    () => [
      { label: 'Grounded answers', value: 'Anthropic + RAG' },
      { label: 'Retrieval', value: 'Hybrid search' },
      { label: 'Documents', value: '1 indexed pdf' },
    ],
    [],
  )

  const handleAsk = async (value) => {
    const trimmed = value.trim()
    if (!trimmed || loading) return

    const userMessage = { role: 'user', content: trimmed }
    setMessages((current) => [...current, userMessage])
    setQuestion('')
    setLoading(true)

    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmed }),
      })

      const data = await response.json()
      if (!response.ok) {
        throw new Error(data.detail || 'The server could not answer that question.')
      }

      setMessages((current) => [
        ...current,
        {
          role: 'assistant',
          content: data.answer,
          sources: data.sources || [],
        },
      ])
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          role: 'assistant',
          content: `I hit an issue while answering: ${error.message}`,
          sources: [],
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-7xl px-4 py-8 lg:px-8">
        <header className="mb-8 rounded-3xl border border-white/10 bg-white/5 p-6 shadow-2xl shadow-cyan-500/10 backdrop-blur-xl">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="mb-2 text-sm font-medium uppercase tracking-[0.3em] text-cyan-300">
                AI Coach RAG
              </p>
              <h1 className="text-3xl font-bold tracking-tight text-white md:text-5xl">
                Ask your document anything.
              </h1>
            </div>
            <button
              type="button"
              onClick={() => handleAsk(starterQuestions[0])}
              className="rounded-full border border-cyan-400/60 bg-cyan-500/10 px-4 py-2 text-sm font-medium text-cyan-200 transition hover:border-cyan-300 hover:bg-cyan-500/20"
            >
              Try guided prompt
            </button>
          </div>

          <div className="mt-8 grid gap-4 md:grid-cols-3">
            {stats.map((stat) => (
              <div key={stat.label} className="rounded-2xl border border-white/10 bg-slate-900/70 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-slate-400">{stat.label}</p>
                <p className="mt-3 text-lg font-semibold text-white">{stat.value}</p>
              </div>
            ))}
          </div>
        </header>

        <div className="grid gap-6 lg:grid-cols-[1.5fr_0.75fr]">
          <section className="rounded-3xl border border-white/10 bg-slate-900/80 p-4 shadow-2xl shadow-fuchsia-500/5">
            <div className="mb-4 flex items-center justify-between border-b border-white/10 pb-3">
              <h2 className="text-lg font-semibold text-white">Conversation</h2>
              <span className="rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-300">
                Live
              </span>
            </div>

            <div className="space-y-4">
              {messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`rounded-2xl border p-4 ${
                    message.role === 'assistant'
                      ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-50'
                      : 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-50'
                  }`}
                >
                  <p className="mb-2 text-[10px] uppercase tracking-[0.25em] text-slate-300">
                    {message.role === 'assistant' ? 'Assistant' : 'You'}
                  </p>
                  <p className="whitespace-pre-wrap leading-7">{message.content}</p>
                </div>
              ))}

              {loading && (
                <div className="rounded-2xl border border-cyan-500/30 bg-cyan-500/10 p-4 text-cyan-100">
                  <div className="flex items-center gap-3">
                    <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-cyan-300" />
                    <span>Thinking through the document context...</span>
                  </div>
                </div>
              )}
            </div>

            <form
              className="mt-6 flex flex-col gap-3 rounded-2xl border border-white/10 bg-slate-950/70 p-3 md:flex-row"
              onSubmit={(event) => {
                event.preventDefault()
                handleAsk(question)
              }}
            >
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask about the story, characters, themes, or actions..."
                className="min-h-[52px] flex-1 rounded-xl border border-white/10 bg-slate-900 px-4 text-sm text-white placeholder:text-slate-400 focus:border-cyan-400 focus:outline-none"
              />
              <button
                type="submit"
                disabled={loading}
                className="min-h-[52px] rounded-xl bg-gradient-to-r from-cyan-500 to-fuchsia-500 px-5 font-semibold text-white transition hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loading ? 'Sending...' : 'Ask'}
              </button>
            </form>
          </section>

          <aside className="rounded-3xl border border-white/10 bg-slate-900/70 p-4 shadow-xl shadow-cyan-500/5">
            <div className="mb-4">
              <p className="text-xs uppercase tracking-[0.25em] text-slate-400">Prompt ideas</p>
            </div>
            <div className="space-y-3">
              {starterQuestions.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => handleAsk(prompt)}
                  className="w-full rounded-2xl border border-white/10 bg-white/5 p-3 text-left text-sm text-slate-200 transition hover:border-cyan-400/60 hover:bg-cyan-500/10"
                >
                  {prompt}
                </button>
              ))}
            </div>

            <div className="mt-8 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-4">
              <p className="text-xs uppercase tracking-[0.2em] text-emerald-300">Context</p>
              <p className="mt-2 text-sm leading-6 text-emerald-50">
                Answers are grounded in the indexed PDF and reranked by relevance before the model responds.
              </p>
            </div>
          </aside>
        </div>
      </div>
    </main>
  )
}

export default App
