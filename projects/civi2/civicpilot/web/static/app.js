// CivicPilot Modern Web Application Script - Cleaned Activity Card Metadata Edition

document.addEventListener('DOMContentLoaded', () => {
  const promptInput = document.getElementById('promptInput');
  const submitBtn = document.getElementById('submitBtn');
  const presetChips = document.querySelectorAll('.preset-chip');
  
  const auditPanel = document.getElementById('auditPanel');
  const toggleAuditBtn = document.getElementById('toggleAuditBtn');
  const toggleChevron = document.getElementById('toggleChevron');
  
  const liveActivityBox = document.getElementById('liveActivityBox');
  const wsStatusBadge = document.getElementById('wsStatusBadge');
  
  const resultsPlaceholder = document.getElementById('resultsPlaceholder');
  const resultsCard = document.getElementById('resultsCard');
  const profileSummaryBox = document.getElementById('profileSummaryBox');
  const schemesGrid = document.getElementById('schemesGrid');
  const traceOutput = document.getElementById('traceOutput');

  let socket = null;
  let currentProfileSummary = "";

  // Collapsible Audit Drawer Handler with robust event propagation
  if (toggleAuditBtn && auditPanel) {
    toggleAuditBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const isCollapsed = auditPanel.classList.toggle('collapsed');
      if (isCollapsed) {
        // Points Left < (Matching Sketch 1)
        toggleChevron.innerHTML = '<polyline points="15 18 9 12 15 6"/>';
      } else {
        // Points Right > (Matching Sketch 2)
        toggleChevron.innerHTML = '<polyline points="9 18 15 12 9 6"/>';
      }
    });
  }

  // Preset pill click handler
  presetChips.forEach(chip => {
    chip.addEventListener('click', () => {
      promptInput.value = chip.getAttribute('data-prompt');
      promptInput.focus();
    });
  });

  // Minimal Agent & Tool Mapping
  function parseMinimalAgentTool(event) {
    const toolName = event.tool_name || 'agent_tool';
    const desc = event.operation_description || '';
    
    let agentRole = 'Agent';
    if (desc.includes(':')) {
      agentRole = desc.split(':')[0].trim();
    } else if (toolName.includes('fast_model')) {
      agentRole = 'Intake_Module';
    } else if (toolName.includes('reasoning_model')) {
      agentRole = 'Planner / Verifier';
    } else if (toolName.includes('search') || toolName.includes('fetch')) {
      agentRole = 'Researcher_Agent';
    } else if (toolName.includes('document')) {
      agentRole = 'Document_Advisor';
    }

    let toolDisplay = toolName;
    if (toolName === 'fast_model_llm') toolDisplay = 'Fast_Model (Groq LLaMA 3.1 8B)';
    else if (toolName === 'reasoning_model_llm') toolDisplay = 'Reasoning_Model (Groq GPT-OSS 120B)';
    else if (toolName === 'tavily_search') toolDisplay = 'Search_Tool (Tavily)';
    else if (toolName === 'jina_fetch') toolDisplay = 'Fetch_Tool (Jina)';
    else if (toolName === 'document_generator') toolDisplay = 'Document_Generator (PDF)';

    return {
      agentRole,
      toolDisplay,
      status: event.status || 'RUNNING',
      elapsed_s: event.elapsed_s || 0.0,
      summary: event.result_summary || 'Processing...'
    };
  }

  // Setup WebSocket connection for streaming trace events
  function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/stream`;
    
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      wsStatusBadge.innerHTML = '<span class="live-dot"></span> LIVE';
    };

    socket.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === 'trace_event') {
          addActivityCard(data);
        }
      } catch (err) {
        console.error('Failed to parse WebSocket trace event:', err);
      }
    };

    socket.onclose = () => {
      wsStatusBadge.innerHTML = '<span style="color: #9CA3AF;">OFFLINE</span>';
      setTimeout(initWebSocket, 2500);
    };
  }

  initWebSocket();

  function addActivityCard(rawEvent) {
    const emptyState = liveActivityBox.querySelector('.empty-state');
    if (emptyState) {
      liveActivityBox.innerHTML = '';
    }

    const t = parseMinimalAgentTool(rawEvent);
    const card = document.createElement('div');
    card.className = 'activity-card';

    const statusUpper = t.status.toUpperCase();
    const pillClass = statusUpper === 'RUNNING' 
      ? 'pill-running' 
      : (statusUpper === 'FAILED' || statusUpper === 'TIMED_OUT') ? 'pill-failed' : 'pill-complete';

    card.innerHTML = `
      <div class="activity-card-header">
        <div class="agent-identity">
          <span class="tool-icon-badge">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#7C3AED" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
            AGENT & TOOL EXECUTION
          </span>
          <span class="agent-role-tag">${escapeHtml(t.agentRole)}</span>
        </div>
        <span class="activity-status-pill ${pillClass}">${escapeHtml(statusUpper)}</span>
      </div>
      <div class="tool-name-line">
        <span class="tool-label">Tool Called:</span>
        <span class="tool-name">${escapeHtml(t.toolDisplay)}</span>
      </div>
      <div class="activity-desc">${escapeHtml(t.summary)}</div>
      <div class="activity-meta">
        <span>Execution Latency: <strong>${t.elapsed_s}s</strong></span>
      </div>
    `;

    liveActivityBox.appendChild(card);
    liveActivityBox.scrollTop = liveActivityBox.scrollHeight;
  }

  function escapeHtml(text) {
    if (!text) return '';
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Smooth Box Expansion Helpers
  function expandBox(el) {
    el.classList.add('expanded');
    el.style.maxHeight = (el.scrollHeight + 1500) + 'px';
  }

  function collapseBox(el) {
    el.classList.remove('expanded');
    el.style.maxHeight = '0px';
  }

  // Form submission with preserved SVG icon layout
  submitBtn.addEventListener('click', async () => {
    const text = promptInput.value.trim();
    if (!text) return;

    submitBtn.disabled = true;
    submitBtn.innerHTML = `
      <svg class="spin-loader" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
      <span>Agents & Tools Executing...</span>
    `;
    if (toggleAuditBtn) toggleAuditBtn.classList.add('generating');

    try {
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: text })
      });

      if (!response.ok) {
        const contentType = response.headers.get('content-type') || '';
        let errorMsg = 'Pipeline processing failed.';
        if (contentType.includes('application/json')) {
          const errData = await response.json();
          errorMsg = errData.detail || errorMsg;
        } else {
          const rawText = await response.text();
          errorMsg = rawText || errorMsg;
        }
        throw new Error(errorMsg);
      }

      const report = await response.json();
      renderReport(report);

    } catch (error) {
      alert(`Error: ${error.message}`);
    } finally {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <span>Analyze Scheme Eligibility</span>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
      `;
      if (toggleAuditBtn) toggleAuditBtn.classList.remove('generating');
    }
  });

  function renderReport(report) {
    if (resultsPlaceholder) resultsPlaceholder.style.display = 'none';
    resultsCard.style.display = 'block';

    currentProfileSummary = report.profile_summary;
    profileSummaryBox.textContent = report.profile_summary;

    // Schemes Grid
    schemesGrid.innerHTML = '';
    report.scheme_candidates.forEach((cand, idx) => {
      const res = report.results[idx] || {};
      const card = document.createElement('div');
      card.className = 'scheme-card';

      let statusBadge = '';
      if (res.overall === 'ELIGIBLE') {
        statusBadge = '<span class="badge-status badge-eligible">ELIGIBLE</span>';
      } else if (res.overall === 'NOT_ELIGIBLE') {
        statusBadge = '<span class="badge-status badge-not-eligible">NOT ELIGIBLE</span>';
      } else {
        statusBadge = '<span class="badge-status badge-needs-info">POSSIBLE (NEEDS INFO)</span>';
      }

      const url = cand.source_urls && cand.source_urls.length > 0 ? cand.source_urls[0] : '#';
      const schemeId = cand.scheme_id || `scheme_${idx+1}`;
      const escapedName = escapeHtml(cand.name).replace(/'/g, "\\'");

      card.innerHTML = `
        <div>
          <div class="scheme-title">${idx + 1}. ${escapeHtml(cand.name)}</div>
          <div class="scheme-badges" style="margin-top: 8px;">
            ${statusBadge}
            <span class="badge-portal">Tier ${cand.priority_tier} Priority</span>
            <span class="badge-portal">Confidence: ${escapeHtml(res.confidence_level || 'MEDIUM')}</span>
          </div>
        </div>
        <div class="card-actions-row">
          <a class="portal-link" href="${escapeHtml(url)}" target="_blank">
            Official Government Portal &rarr;
          </a>
          <div style="display: flex; gap: 8px; flex-wrap: wrap;">
            <button class="ask-btn" onclick="toggleAskBox('${schemeId}', '${escapedName}', this)">
              Ask AI
            </button>
            <button class="eligibility-btn" onclick="fetchSchemeEligibility('${schemeId}', '${escapedName}', this)">
              Eligibility Criteria
            </button>
            <button class="view-docs-btn" onclick="fetchSchemeDocuments('${schemeId}', '${escapedName}', this)">
              View Required Documents
            </button>
            <button class="doc-download-btn" onclick="downloadOnDemandPdf('${schemeId}', '${escapedName}', this)">
              PDF Guide
            </button>
          </div>
        </div>
        <div id="askBox-${schemeId}" class="ask-box"></div>
        <div id="critBox-${schemeId}" class="crit-box"></div>
        <div id="docsBox-${schemeId}" class="docs-box"></div>
      `;

      schemesGrid.appendChild(card);
    });

    if (traceOutput) traceOutput.textContent = report.performance_trace;

    // Smooth Auto-Scroll to align "Scheme Eligibility & Verification Report" at the top
    setTimeout(() => {
      if (resultsCard) {
        resultsCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      const rightPane = document.querySelector('.workspace-right-pane');
      if (rightPane) {
        rightPane.scrollTo({ top: 0, behavior: 'smooth' });
      }
    }, 100);
  }

  // Global handler for scheme Q&A box toggle with smooth animation
  window.toggleAskBox = function(schemeId, schemeName, btnElement) {
    const askBox = document.getElementById(`askBox-${schemeId}`);
    if (!askBox) return;

    if (askBox.classList.contains('expanded')) {
      collapseBox(askBox);
      btnElement.innerHTML = 'Ask AI';
      return;
    }

    renderAskBox(askBox, schemeId, schemeName);
    expandBox(askBox);
    btnElement.innerHTML = 'Close Ask AI';
  };

  function renderAskBox(container, schemeId, schemeName) {
    container.innerHTML = `
      <div class="ask-header">
        Ask AI About ${escapeHtml(schemeName)}:
      </div>
      <div class="ask-presets">
        <button class="ask-chip" onclick="askPresetQuestion('${schemeId}', '${escapeHtml(schemeName).replace(/'/g, "\\'")}', 'How do I apply for this scheme?')">
          How to apply?
        </button>
        <button class="ask-chip" onclick="askPresetQuestion('${schemeId}', '${escapeHtml(schemeName).replace(/'/g, "\\'")}', 'Is an income certificate mandatory?')">
          Income certificate required?
        </button>
        <button class="ask-chip" onclick="askPresetQuestion('${schemeId}', '${escapeHtml(schemeName).replace(/'/g, "\\'")}', 'What is the application deadline or last date?')">
          Application deadline?
        </button>
        <button class="ask-chip" onclick="askPresetQuestion('${schemeId}', '${escapeHtml(schemeName).replace(/'/g, "\\'")}', 'How long does application approval take?')">
          Approval time?
        </button>
      </div>
      <div class="ask-input-row">
        <input type="text" id="askInput-${schemeId}" class="ask-input" placeholder="Ask a specific question about this scheme..." onkeypress="handleAskKeypress(event, '${schemeId}', '${escapeHtml(schemeName).replace(/'/g, "\\'")}')">
        <button class="ask-submit-btn" onclick="sendCustomQuestion('${schemeId}', '${escapeHtml(schemeName).replace(/'/g, "\\'")}')">
          Send
        </button>
      </div>
      <div id="answerBubble-${schemeId}" class="answer-bubble" style="display: none;"></div>
    `;
  }

  window.handleAskKeypress = function(e, schemeId, schemeName) {
    if (e.key === 'Enter') {
      sendCustomQuestion(schemeId, schemeName);
    }
  };

  window.askPresetQuestion = function(schemeId, schemeName, questionText) {
    const input = document.getElementById(`askInput-${schemeId}`);
    if (input) input.value = questionText;
    sendCustomQuestion(schemeId, schemeName);
  };

  window.sendCustomQuestion = async function(schemeId, schemeName) {
    const input = document.getElementById(`askInput-${schemeId}`);
    const bubble = document.getElementById(`answerBubble-${schemeId}`);
    const askBox = document.getElementById(`askBox-${schemeId}`);
    if (!input || !bubble) return;

    const question = input.value.trim();
    if (!question) return;

    bubble.style.display = 'block';
    bubble.innerHTML = '<em>Groq GPT-OSS 120B AI thinking...</em>';
    if (askBox) expandBox(askBox);

    try {
      const resp = await fetch('/api/scheme-ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scheme_id: schemeId,
          scheme_name: schemeName,
          question: question,
          profile_summary: currentProfileSummary
        })
      });

      if (!resp.ok) {
        throw new Error('Failed to fetch answer.');
      }

      const data = await resp.json();
      bubble.innerHTML = `
        <div style="font-weight: 600; color: #1E3A8A; margin-bottom: 4px;">Q: ${escapeHtml(data.question)}</div>
        <div><strong>AI Answer:</strong> ${escapeHtml(data.answer)}</div>
      `;
      if (askBox) expandBox(askBox);

    } catch (err) {
      bubble.innerHTML = `<span style="color: #991B1B;">Error: ${escapeHtml(err.message)}</span>`;
    }
  };

  // Global handler for fetching eligibility criteria on-demand with smooth animation
  window.fetchSchemeEligibility = async function(schemeId, schemeName, btnElement) {
    const critBox = document.getElementById(`critBox-${schemeId}`);
    if (!critBox) return;

    if (critBox.classList.contains('expanded')) {
      collapseBox(critBox);
      btnElement.innerHTML = 'Eligibility Criteria';
      return;
    }

    btnElement.disabled = true;
    btnElement.innerHTML = 'Extracting Criteria...';

    try {
      const resp = await fetch('/api/scheme-eligibility', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scheme_id: schemeId,
          scheme_name: schemeName,
          profile_summary: currentProfileSummary
        })
      });

      if (!resp.ok) {
        throw new Error('Failed to extract eligibility criteria.');
      }

      const data = await resp.json();
      renderCritBox(critBox, data);
      expandBox(critBox);
      btnElement.innerHTML = 'Hide Criteria';

    } catch (err) {
      alert(`Error: ${err.message}`);
      btnElement.innerHTML = 'Eligibility Criteria';
    } finally {
      btnElement.disabled = false;
    }
  };

  function renderCritBox(container, data) {
    const age = data.age_limit || 'As per scheme rules';
    const income = data.income_limit || 'As per scheme limits';
    const group = data.target_group || 'General Citizens';
    const rules = data.key_rules || [];

    let rulesHtml = rules.map(rule => `
      <div class="rule-bullet">• ${escapeHtml(rule)}</div>
    `).join('');

    container.innerHTML = `
      <div class="crit-header">
        Scheme Eligibility Criteria Summary:
      </div>
      <div class="crit-badge-row">
        <span class="crit-pill"><strong>Age:</strong> ${escapeHtml(age)}</span>
        <span class="crit-pill"><strong>Income Limit:</strong> ${escapeHtml(income)}</span>
        <span class="crit-pill"><strong>Target:</strong> ${escapeHtml(group)}</span>
      </div>
      ${rules.length > 0 ? `
        <div style="font-size: 12px; font-weight: 600; color: #1E3A8A; margin-top: 8px; margin-bottom: 4px;">Key Qualifier Rules:</div>
        ${rulesHtml}
      ` : ''}
    `;
  }

  // Global handler for fetching required documents on-demand with per-document guidance
  window.fetchSchemeDocuments = async function(schemeId, schemeName, btnElement) {
    const docsBox = document.getElementById(`docsBox-${schemeId}`);
    if (!docsBox) return;

    if (docsBox.classList.contains('expanded')) {
      collapseBox(docsBox);
      btnElement.innerHTML = 'View Required Documents';
      return;
    }

    btnElement.disabled = true;
    btnElement.innerHTML = 'Generating Required Documents...';

    try {
      const resp = await fetch('/api/scheme-documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scheme_id: schemeId,
          scheme_name: schemeName,
          profile_summary: currentProfileSummary
        })
      });

      if (!resp.ok) {
        throw new Error('Failed to generate documents.');
      }

      const data = await resp.json();
      renderDocsBox(docsBox, data, schemeName, schemeId);
      expandBox(docsBox);
      btnElement.innerHTML = 'Hide Required Documents';

    } catch (err) {
      alert(`Error: ${err.message}`);
      btnElement.innerHTML = 'View Required Documents';
    } finally {
      btnElement.disabled = false;
    }
  };

  function renderDocsBox(container, data, schemeName, schemeId) {
    const docs = data.required_documents || [];
    const steps = data.application_steps || [];

    let docsHtml = docs.map((doc, idx) => {
      const docId = `${schemeId}_doc_${idx}`;
      const escapedDocName = escapeHtml(doc).replace(/'/g, "\\'");
      const escapedSchemeName = escapeHtml(schemeName).replace(/'/g, "\\'");

      return `
        <div class="doc-item-wrapper">
          <div class="doc-item">
            <div class="doc-title-side">
              <span class="doc-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg></span>
              <span>${escapeHtml(doc)}</span>
            </div>
            <button class="doc-howto-btn" onclick="fetchDocHowTo('${docId}', '${escapedDocName}', '${escapedSchemeName}', this)">
              How to get?
            </button>
          </div>
          <div id="docHowtoBox-${docId}" class="doc-howto-box"></div>
        </div>
      `;
    }).join('');

    let stepsHtml = steps.map((step, idx) => `
      <div class="step-item">
        <strong>Step ${idx + 1}:</strong> ${escapeHtml(step)}
      </div>
    `).join('');

    container.innerHTML = `
      <div class="docs-header">
        Official Required Documents Checklist:
      </div>
      <div class="docs-list">
        ${docsHtml}
      </div>
      ${steps.length > 0 ? `
        <div class="steps-container">
          <div style="font-size: 13px; font-weight: 600; color: #1E3A8A; margin-bottom: 6px;">Application Process Steps:</div>
          ${stepsHtml}
        </div>
      ` : ''}
    `;
  }

  // Global handler for fetching guidance on how to obtain a specific document
  window.fetchDocHowTo = async function(docId, docName, schemeName, btnElement) {
    const box = document.getElementById(`docHowtoBox-${docId}`);
    if (!box) return;

    if (box.classList.contains('expanded')) {
      collapseBox(box);
      btnElement.innerHTML = 'How to get?';
      return;
    }

    btnElement.disabled = true;
    btnElement.innerHTML = 'Fetching...';

    try {
      const resp = await fetch('/api/document-howto', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document_name: docName, scheme_name: schemeName })
      });

      if (!resp.ok) {
        throw new Error('Failed to fetch document guidance.');
      }

      const data = await resp.json();
      box.innerHTML = `
        <div class="howto-header">How to obtain ${escapeHtml(data.document_name || docName)}:</div>
        <div class="howto-row"><strong>Issuing Authority:</strong> ${escapeHtml(data.issuing_authority || 'Revenue Authority / CSC')}</div>
        <div class="howto-row"><strong>Required Proofs:</strong> ${escapeHtml(data.required_proofs || 'Aadhaar Card, Identity Proof')}</div>
        <div class="howto-row"><strong>How to Apply:</strong> ${escapeHtml(data.process_steps || 'Apply online or visit CSC Kendra.')}</div>
      `;

      expandBox(box);

      // Re-expand parent container height
      const parentDocsBox = box.closest('.docs-box');
      if (parentDocsBox) expandBox(parentDocsBox);

      btnElement.innerHTML = 'Hide Guide';

    } catch (err) {
      alert(`Error: ${err.message}`);
      btnElement.innerHTML = 'How to get?';
    } finally {
      btnElement.disabled = false;
    }
  };
});

window.downloadOnDemandPdf = async function(schemeId, schemeName, btnElement) {
  btnElement.disabled = true;
  btnElement.innerHTML = 'Generating PDF...';

  try {
    const resp = await fetch('/api/generate-pdf-guide', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scheme_id: schemeId,
        scheme_name: schemeName,
        profile_summary: typeof currentProfileSummary !== 'undefined' ? currentProfileSummary : ''
      })
    });

    if (!resp.ok) {
      throw new Error('Failed to generate PDF guide.');
    }

    const blob = await resp.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `CivicPilot_Guide_${schemeId}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);

  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btnElement.disabled = false;
    btnElement.innerHTML = 'PDF Guide';
  }
};
