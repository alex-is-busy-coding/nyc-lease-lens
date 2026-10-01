// Safety rule: text from users and the server goes in with textContent. The agent's Markdown is the
// one exception, and it is sanitized with DOMPurify before it becomes HTML.

const messages = document.getElementById("messages");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const clearBtn = document.getElementById("clear-btn");
let sessionId = null;
let busy = false;

// Clearing mid-request would let the late reply restore the old session.
function setBusy(value) {
    busy = value;
    sendBtn.disabled = value;
    clearBtn.disabled = value;
}

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

function addMessage(role, text, isLoading = false) {
    const message = el("div", `msg ${role === "you" ? "user" : "assistant"}${isLoading ? " loading" : ""}`);
    message.append(el("div", "role", role), el("div", "content", text));
    messages.appendChild(message);
    messages.scrollTop = messages.scrollHeight;
    return message;
}

function renderMarkdown(text) {
    const answer = el("div", "answer");
    if (window.marked && window.DOMPurify) {
        // Answers never need images or forms; an image URL in a reply would make the browser fetch it.
        answer.innerHTML = DOMPurify.sanitize(marked.parse(text), {
            FORBID_TAGS: ["img", "style", "form", "input", "button", "iframe"],
        });
    } else {
        answer.textContent = text; // the libraries didn't load: plain text is still readable
    }
    return answer;
}

// --- Grade card, from the structured score_building_risk result ---

function gradeResult(toolCalls) {
    const call = [...toolCalls].reverse().find((c) => c.name === "score_building_risk");
    if (!call) return null;
    try {
        const result = JSON.parse(call.result);
        return "grade" in result ? result : null; // ambiguous addresses and errors have no grade
    } catch {
        return null;
    }
}

function renderGradeCard(result) {
    const card = el("section", "grade-card");
    card.setAttribute("aria-label", "Building grade");

    const graded = typeof result.grade === "string";
    const letter = el("div", `grade-letter grade-${graded ? result.grade : "none"}`, graded ? result.grade : "N/A");
    letter.setAttribute("aria-label", graded ? `Grade ${result.grade}` : "Not graded");

    const summary = el("div", "grade-summary");
    summary.append(el("div", "address", result.address || ""));
    summary.append(
        el(
            "div",
            "score",
            graded ? `Score ${result.score} of 100 · 0 means no red flags` : (result.notes || []).join(" "),
        ),
    );
    card.append(letter, summary);

    const items = el("ul");
    for (const flag of result.red_flags || []) {
        const item = el("li");
        item.append(el("span", "points", `+${flag.points}`), document.createTextNode(` ${flag.finding}`));
        items.append(item);
    }
    for (const sign of result.good_signs || []) items.append(el("li", "good", sign));
    for (const gap of result.data_gaps || []) items.append(el("li", "gap", `Not checked: ${gap}`));
    if (items.children.length) card.append(items);
    return card;
}

// --- Where the answer comes from, under the answer ---

function renderSources(sources, grade) {
    const footer = el("footer", "sources");
    const line = el("p");
    line.append(el("strong", "", "Sources: "));
    sources.forEach((source, i) => {
        if (i) line.append(" · ");
        const link = el("a", "", source.name);
        link.href = source.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.title = `Updated ${source.updated.toLowerCase()} by the publisher`;
        line.append(link);
    });
    line.append(" — NYC Open Data, fetched when you asked.");
    footer.append(line);
    if (grade && grade.bbl) {
        // The city's IDs for the building, so readers can look it up themselves.
        footer.append(el("p", "", `Building: BBL ${grade.bbl}${grade.bin ? ` · BIN ${grade.bin}` : ""}`));
    }
    return footer;
}

// --- The raw tool calls, for developers: open the page with ?debug=1 ---

const DEBUG = new URLSearchParams(location.search).has("debug");

function renderChecks(toolCalls) {
    const details = el("details", "checks");
    details.append(el("summary", "", `Data checked (${toolCalls.length})`));
    for (const call of toolCalls) {
        let result = call.result;
        try {
            result = JSON.stringify(JSON.parse(call.result), null, 2);
        } catch {
            // not JSON: show as is
        }
        details.append(el("pre", "", `${call.name}(${JSON.stringify(call.args)})\n${result}`));
    }
    return details;
}

// --- Sending, clearing and the welcome message ---

async function send() {
    const text = userInput.value.trim();
    if (!text || busy) return;

    userInput.value = "";
    setBusy(true);
    addMessage("you", text);
    const reply = addMessage("assistant", "Checking city records… this can take up to a minute.", true);
    const content = reply.querySelector(".content");

    try {
        const res = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: text, session_id: sessionId }),
        });
        if (!res.ok) throw new Error(`the server answered ${res.status}`);
        const data = await res.json();
        sessionId = data.session_id;

        const toolCalls = data.tool_calls || [];
        const grade = gradeResult(toolCalls);
        content.replaceChildren();
        if (grade) content.append(renderGradeCard(grade));
        content.append(renderMarkdown(data.response || ""));
        if (data.sources && data.sources.length) content.append(renderSources(data.sources, grade));
        if (DEBUG && toolCalls.length) content.append(renderChecks(toolCalls));
    } catch (e) {
        content.textContent = `Something went wrong: ${e.message}. Please try again.`;
    }
    reply.classList.remove("loading");
    messages.scrollTop = messages.scrollHeight;
    setBusy(false);
    userInput.focus();
}

async function clearChat() {
    if (busy) return;
    const previous = sessionId;
    sessionId = null;
    messages.replaceChildren();
    showWelcome();
    userInput.value = "";
    userInput.focus();
    if (!previous) return;
    try {
        await fetch("/clear?session_id=" + encodeURIComponent(previous), { method: "POST" });
    } catch (e) {
        // The screen is already cleared and the next message starts a new session.
        console.warn("Could not clear the session on the server:", e);
    }
}

// Shown on load and after Clear. Rendered here, not by the model, so it's instant and free.
function showWelcome() {
    const welcome = document.getElementById("welcome").content.cloneNode(true);
    for (const button of welcome.querySelectorAll("[data-address]")) {
        button.addEventListener("click", () => {
            userInput.value = "Check " + button.dataset.address;
            send();
        });
    }
    messages.appendChild(welcome);
}

sendBtn.addEventListener("click", send);
clearBtn.addEventListener("click", clearChat);
userInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
});
showWelcome();
userInput.focus();
