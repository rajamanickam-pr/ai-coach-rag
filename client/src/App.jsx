import { useCallback, useEffect, useState } from 'react'
import './App.css'

const DEFAULT_COLLECTION = 'karumegam_collection'
const starterQuestions = [
  'What are the main ideas in these documents?',
  'Summarize the most important recommendations.',
  'What evidence supports the central conclusion?',
]

async function readResponse(response) {
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((issue) => issue.msg).join('; ')
      : data.detail
    throw new Error(typeof detail === 'string' ? detail : 'The request could not be completed.')
  }
  return data
}

function App() {
  const [user, setUser] = useState(null)
  const [csrfToken, setCsrfToken] = useState('')
  const [authLoading, setAuthLoading] = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [notice, setNotice] = useState('')
  const [activeView, setActiveView] = useState('chat')
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [conversations, setConversations] = useState([])
  const [conversationId, setConversationId] = useState('')
  const [collections, setCollections] = useState([DEFAULT_COLLECTION])
  const [collectionName, setCollectionName] = useState(DEFAULT_COLLECTION)
  const [adminCollection, setAdminCollection] = useState(DEFAULT_COLLECTION)
  const [documents, setDocuments] = useState([])
  const [adminUsers, setAdminUsers] = useState([])
  const [newUserUsername, setNewUserUsername] = useState('')
  const [newUserEmail, setNewUserEmail] = useState('')
  const [newUserPassword, setNewUserPassword] = useState('')
  const [newUserRole, setNewUserRole] = useState('member')
  const [adminStatus, setAdminStatus] = useState('')
  const [adminError, setAdminError] = useState('')
  const [adminLoading, setAdminLoading] = useState(false)
  const [uploading, setUploading] = useState(false)

  const requestOptions = useCallback((method = 'GET', body) => {
    const headers = {}
    if (body instanceof FormData) headers['X-CSRF-Token'] = csrfToken
    else if (body !== undefined) {
      headers['Content-Type'] = 'application/json'
      headers['X-CSRF-Token'] = csrfToken
    }
    return { method, headers, ...(body !== undefined ? { body: body instanceof FormData ? body : JSON.stringify(body) } : {}) }
  }, [csrfToken])

  const refreshConversations = useCallback(async () => {
    const data = await readResponse(await fetch('/api/conversations'))
    const rows = data.conversations || []
    setConversations(rows)
    return rows
  }, [])

  const openConversation = useCallback(async (id) => {
    setLoading(true)
    setMessages([])
    setConversationId('')
    try {
      const data = await readResponse(await fetch(`/api/conversations/${id}`))
      setConversationId(data.id)
      setCollectionName(data.collection_name)
      setMessages(data.messages || [])
      setNotice('')
      setActiveView('chat')
    } catch (error) {
      setNotice(error.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const initializeWorkspace = useCallback(async () => {
    const [conversationData, collectionData] = await Promise.all([
      fetch('/api/conversations').then(readResponse),
      fetch('/api/collections').then(readResponse),
    ])
    const rows = conversationData.conversations || []
    setConversations(rows)
    setCollections(collectionData.collections?.length ? collectionData.collections : [DEFAULT_COLLECTION])
    if (rows.length) await openConversation(rows[0].id)
    else {
      setConversationId('')
      setMessages([])
    }
  }, [openConversation])

  useEffect(() => {
    let active = true
    fetch('/api/auth/me')
      .then(readResponse)
      .then(async (data) => {
        if (!active) return
        setUser(data.user)
        setCsrfToken(data.csrf_token || '')
        await initializeWorkspace()
      })
      .catch(() => { if (active) setUser(null) })
      .finally(() => { if (active) setAuthLoading(false) })
    return () => { active = false }
  }, [initializeWorkspace])

  const loadAdminData = async (selectedCollection = adminCollection) => {
    setAdminLoading(true)
    setAdminError('')
    try {
      const [collectionData, userData] = await Promise.all([
        fetch('/api/admin/collections').then(readResponse),
        fetch('/api/admin/users').then(readResponse),
      ])
      const available = collectionData.collections?.length ? collectionData.collections : [DEFAULT_COLLECTION]
      setCollections(available)
      setAdminUsers(userData.users || [])
      const activeCollection = selectedCollection || available[0]
      const data = await readResponse(await fetch(`/api/admin/documents?collection_name=${encodeURIComponent(activeCollection)}`))
      setAdminCollection(activeCollection)
      setDocuments(data.documents || [])
    } catch (error) {
      setAdminError(error.message)
    } finally {
      setAdminLoading(false)
    }
  }

  const handleLogin = async (event) => {
    event.preventDefault()
    setNotice('')
    setAuthLoading(true)
    try {
      const data = await readResponse(await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      }))
      setUser(data.user)
      setCsrfToken(data.csrf_token)
      setPassword('')
      await initializeWorkspace()
    } catch (error) {
      setNotice(error.message)
    } finally {
      setAuthLoading(false)
    }
  }

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', requestOptions('POST', {}))
    } finally {
      setUser(null)
      setCsrfToken('')
      setConversations([])
      setConversationId('')
      setMessages([])
      setDocuments([])
      setQuestion('')
      setNotice('')
      setActiveView('chat')
    }
  }

  const createConversation = async () => {
    setNotice('')
    try {
      const data = await readResponse(await fetch('/api/conversations', requestOptions('POST', {
        title: 'New conversation', collection_name: collectionName,
      })))
      setConversationId(data.id)
      setMessages([])
      setCollectionName(data.collection_name)
      await refreshConversations()
    } catch (error) {
      setNotice(error.message)
    }
  }

  const deleteConversation = async (id) => {
    if (!window.confirm('Delete this conversation and its stored messages?')) return
    try {
      await readResponse(await fetch(`/api/conversations/${id}`, requestOptions('DELETE', {})))
      const remaining = await refreshConversations()
      if (conversationId === id) {
        setConversationId('')
        setMessages([])
        if (remaining.length) await openConversation(remaining[0].id)
      }
    } catch (error) {
      setNotice(error.message)
    }
  }

  const handleAsk = async (value) => {
    const trimmed = value.trim()
    if (!trimmed || loading) return
    setNotice('')
    let activeId = conversationId
    if (!activeId) {
      try {
        const created = await readResponse(await fetch('/api/conversations', requestOptions('POST', {
          title: trimmed.slice(0, 80), collection_name: collectionName,
        })))
        activeId = created.id
        setConversationId(activeId)
        await refreshConversations()
      } catch (error) {
        setNotice(error.message)
        return
      }
    }
    setQuestion('')
    setLoading(true)
    try {
      await readResponse(await fetch(`/api/conversations/${activeId}/ask`, requestOptions('POST', {
        question: trimmed, collection_name: collectionName,
      })))
      await openConversation(activeId)
      await refreshConversations()
    } catch (error) {
      setNotice(error.message)
    } finally {
      setLoading(false)
    }
  }

  const handleUpload = async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    setUploading(true)
    setAdminError('')
    setAdminStatus('')
    try {
      const data = await readResponse(await fetch('/api/admin/documents', requestOptions('POST', new FormData(form))))
      setAdminStatus(data.status === 'duplicate' ? 'This document is already indexed; no duplicate chunks were added.' : `Indexed ${data.chunks} chunks from ${data.name}.`)
      setCollectionName(data.collection)
      setAdminCollection(data.collection)
      await loadAdminData(data.collection)
      form.reset()
    } catch (error) {
      setAdminError(error.message)
    } finally {
      setUploading(false)
    }
  }

  const handleCreateUser = async (event) => {
    event.preventDefault()
    setAdminError('')
    setAdminStatus('')
    try {
      const data = await readResponse(await fetch('/api/admin/users', requestOptions('POST', {
        username: newUserUsername,
        email: newUserEmail,
        password: newUserPassword,
        role: newUserRole,
      })))
      setAdminStatus(`Created ${data.role} account for ${data.username}.`)
      setNewUserUsername('')
      setNewUserEmail('')
      setNewUserPassword('')
      await loadAdminData()
    } catch (error) {
      setAdminError(error.message)
    }
  }

  if (authLoading) return <main className="app-frame auth-frame"><p className="eyebrow">AI COACH · SECURE WORKSPACE</p><p>Restoring your session...</p></main>

  if (!user) {
    return (
      <main className="app-frame auth-frame">
        <section className="login-panel">
          <a className="wordmark" href="#login"><span className="wordmark-mark">AC</span><span>AI Coach <small>DOCUMENT INTELLIGENCE</small></span></a>
          <p className="eyebrow">SECURE WORKSPACE</p><h1>Sign in to continue.</h1>
          <form className="login-form" onSubmit={handleLogin}>
            <label className="field">Username<input type="text" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={64} required /></label>
            <label className="field">Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
            {notice && <p className="notice error" role="alert">{notice}</p>}
            <button className="primary-button" disabled={authLoading} type="submit">{authLoading ? 'Signing in...' : 'Sign in'}</button>
          </form>
        </section>
      </main>
    )
  }

  const isAdmin = user.permissions.includes('documents:write')

  return (
    <main className="app-frame"><div className="app-container">
      <header className="topbar">
        <a className="wordmark" href="#chat" onClick={() => setActiveView('chat')}><span className="wordmark-mark">AC</span><span>AI Coach <small>DOCUMENT INTELLIGENCE</small></span></a>
        <nav className="view-tabs" aria-label="Main navigation">
          <button className={activeView === 'chat' ? 'tab active' : 'tab'} onClick={() => setActiveView('chat')} type="button">Chat</button>
          {isAdmin && <button className={activeView === 'admin' ? 'tab active' : 'tab'} onClick={() => { setActiveView('admin'); loadAdminData() }} type="button">Admin</button>}
        </nav>
        <div className="account-controls"><span>{user.username}<small>{user.role}</small></span><button className="secondary-button" onClick={handleLogout} type="button">Sign out</button></div>
      </header>

      {activeView === 'chat' ? (
        <section className="workspace">
          <div className="page-heading"><div><p className="eyebrow">RETRIEVAL WORKSPACE</p><h1>Ask across your knowledge base.</h1><p className="subheading">Grounded answers with isolated, persistent conversation context.</p></div>
            <label className="collection-picker"><span>Collection</span><select value={collectionName} onChange={(event) => setCollectionName(event.target.value)}>{collections.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          </div>
          {notice && <p className="notice error" role="alert">{notice}</p>}
          <div className="chat-layout">
            <aside className="conversation-rail"><div className="rail-heading"><p className="eyebrow">YOUR CHATS</p><button className="icon-button" aria-label="New conversation" title="New conversation" onClick={createConversation} type="button">+</button></div>
              <button className="new-conversation-button" onClick={createConversation} type="button">＋ New conversation</button>
              <div className="conversation-list">{conversations.map((item) => <div className={item.id === conversationId ? 'conversation-item selected' : 'conversation-item'} key={item.id}>
                <button onClick={() => openConversation(item.id)} type="button"><strong>{item.title}</strong><small>{new Date(item.updated_at).toLocaleDateString()}</small></button>
                <button className="conversation-delete" aria-label={`Delete ${item.title}`} title="Delete conversation" onClick={() => deleteConversation(item.id)} type="button">×</button>
              </div>)}</div>
            </aside>
            <section className="conversation-panel" aria-label="Conversation">
              <div className="panel-heading"><div><h2>{conversations.find((item) => item.id === conversationId)?.title || 'New conversation'}</h2><span className="session-label">Messages are stored in your account</span></div><span className="live-indicator"><i /> Ready</span></div>
              <div className="message-list" aria-live="polite">
                {!messages.length && <div className="empty-chat"><span>01</span><p>Start a conversation. Document context and follow-up memory stay scoped to this chat.</p></div>}
                {messages.map((message) => <article className={`message ${message.role}`} key={message.id}>
                  <p className="message-role">{message.role === 'assistant' ? 'AI COACH' : 'YOU'}</p><p className="message-content">{message.content}</p>
                  {message.sources?.length > 0 && <div className="source-list">{message.sources.map((source) => <details key={source.id}><summary>{source.source} · passage {source.chunk_index + 1} · {source.score}</summary><p>{source.text}</p></details>)}</div>}
                </article>)}
                {loading && <div className="thinking"><span /> Searching passages and composing an answer...</div>}
              </div>
              <form className="question-form" onSubmit={(event) => { event.preventDefault(); handleAsk(question) }}><label className="sr-only" htmlFor="question">Your question</label>
                <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={4000} placeholder="Ask a question about your documents..." rows={2} /><button className="primary-button" disabled={loading || !question.trim()} type="submit">{loading ? 'Working...' : 'Send question'}</button>
              </form>
            </section>
            <aside className="prompt-rail"><p className="eyebrow">SUGGESTED QUESTIONS</p>{starterQuestions.map((prompt) => <button className="prompt-button" key={prompt} onClick={() => handleAsk(prompt)} type="button">{prompt}<span aria-hidden="true">↗</span></button>)}<div className="context-note"><span className="note-mark">01</span><p>Only recent turns from this conversation are supplied as memory. Other chats remain separate.</p></div></aside>
          </div>
        </section>
      ) : (
        <section className="workspace admin-workspace"><div className="page-heading"><div><p className="eyebrow">KNOWLEDGE BASE</p><h1>Document administration.</h1><p className="subheading">Add a PDF to PostgreSQL pgvector and inspect indexed documents.</p></div></div>
          {adminError && <p className="notice error" role="alert">{adminError}</p>}{adminStatus && <p className="notice success" role="status">{adminStatus}</p>}
          <div className="admin-layout">
            <section className="admin-panel"><div className="panel-heading"><div><h2>Ingest a PDF</h2><span className="session-label">PDF only · maximum 20 MB · selectable text required</span></div></div>
              <form className="upload-form" onSubmit={handleUpload}>
                <label className="field full-width">Document file<input name="file" type="file" accept="application/pdf,.pdf" required /></label>
                <label className="field">Collection name<input name="collection_name" value={adminCollection} onChange={(event) => setAdminCollection(event.target.value)} list="collection-options" minLength={3} maxLength={63} pattern="[A-Za-z0-9][A-Za-z0-9_-]{2,62}" required /><datalist id="collection-options">{collections.map((item) => <option key={item} value={item} />)}</datalist></label>
                <label className="field">Duplicate handling<select name="duplicate_behavior" defaultValue="skip"><option value="skip">Skip identical content</option><option value="replace">Replace same-name document</option><option value="allow">Allow another copy</option></select></label>
                <label className="field">Chunk size<input name="chunk_size" type="number" min="200" max="3000" defaultValue="1000" required /></label><label className="field">Chunk overlap<input name="chunk_overlap" type="number" min="0" max="1499" defaultValue="200" required /></label>
                <label className="field">Display title<input name="title" maxLength="160" placeholder="Defaults to the filename" /></label><label className="field">Tags<input name="tags" maxLength="500" placeholder="Comma-separated" /></label>
                <label className="field full-width">Description<textarea name="description" maxLength="1000" rows={3} placeholder="Optional notes for administrators" /></label>
                <div className="upload-actions"><p>Embeddings run through Ollama and are stored in PostgreSQL.</p><button className="primary-button" disabled={uploading} type="submit">{uploading ? 'Embedding and indexing...' : 'Upload and index'}</button></div>
              </form>
            </section>
            <section className="admin-panel inventory-panel"><div className="panel-heading"><div><h2>Indexed documents</h2><span className="session-label">{adminCollection} · {documents.length} documents</span></div><button className="icon-button" aria-label="Refresh document list" title="Refresh document list" onClick={() => loadAdminData()} type="button">↻</button></div>
              {adminLoading ? <p className="empty-state">Loading documents...</p> : documents.length ? <ul className="document-list">{documents.map((document) => <li key={document.document_id}><span className="document-icon">PDF</span><span className="document-copy"><strong>{document.title || document.name}</strong><small>{document.name} · {document.chunks} chunks · {document.page_count} pages</small>{document.description && <small>{document.description}</small>}</span><button className="conversation-delete" aria-label={`Delete ${document.name}`} title="Delete document" onClick={async () => { if (window.confirm(`Delete ${document.name} and its indexed chunks?`)) { await fetch(`/api/admin/documents/${document.document_id}`, requestOptions('DELETE', {})); await loadAdminData() } }} type="button">×</button></li>)}</ul> : <p className="empty-state">No indexed documents in this collection yet.</p>}
            </section>
          </div>
          <section className="admin-panel user-management"><div className="panel-heading"><div><h2>Accounts and roles</h2><span className="session-label">Role-based access control</span></div></div>
            <form className="user-create-form" onSubmit={handleCreateUser}>
              <label className="field">Username<input type="text" value={newUserUsername} onChange={(event) => setNewUserUsername(event.target.value)} minLength={3} maxLength={64} pattern="[A-Za-z0-9_.-]+" required /></label>
              <label className="field">Email address<input type="email" value={newUserEmail} onChange={(event) => setNewUserEmail(event.target.value)} required /></label>
              <label className="field">Temporary password<input type="password" minLength={12} maxLength={256} value={newUserPassword} onChange={(event) => setNewUserPassword(event.target.value)} autoComplete="new-password" required /></label>
              <label className="field">Role<select value={newUserRole} onChange={(event) => setNewUserRole(event.target.value)}><option value="member">Member</option><option value="admin">Admin</option></select></label>
              <button className="secondary-button" type="submit">Create account</button>
            </form>
            <ul className="user-list">{adminUsers.map((account) => <li key={account.id}><span>{account.username} · {account.email}</span><span className={`role-label ${account.role}`}>{account.role}</span></li>)}</ul>
          </section>
        </section>
      )}
      <footer className="app-footer"><span>POSTGRESQL · PGVECTOR</span><span>{user.username} · {user.role.toUpperCase()}</span></footer>
    </div></main>
  )
}

export default App
