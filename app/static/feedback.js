/* Action feedback belongs beside its own control, never in a shared footer.
   Each async handler captures its own writer before awaiting a request. This
   prevents a later click or screen change from sending its result elsewhere. */
"use strict";
const feedbackWriters = new WeakMap();
const feedbackFallbacks = new WeakMap();
let feedbackNotice;
let feedbackNoticeTimer;

function clearFeedbackNotice() {
  clearTimeout(feedbackNoticeTimer);
  if(feedbackNotice)feedbackNotice.hidden=true;
}

function feedbackFor(anchor) {
  if(anchor.id==="bulk-form")anchor=document.getElementById("draft-status");
  if(feedbackWriters.has(anchor))return feedbackWriters.get(anchor);
  const region=anchor.closest("[data-screen],#auth-panel,#pin-panel,header");
  const fallbacks=[];
  for(let parent=anchor.parentElement;parent;parent=parent.parentElement)
    if(parent.matches("details,section,[data-screen]"))fallbacks.push(parent);
  let node;
  function ensureNode() {
    if(node?.isConnected&&(!anchor.hidden||region?.hidden))return node;
    if(anchor.isConnected&&anchor.matches('[role="status"],[role="alert"]'))node=anchor;
    else {
      node=document.createElement("p");
      node.setAttribute("role","status");
      node.setAttribute("aria-live","polite");
      node.setAttribute("aria-atomic","true");
      if(anchor.isConnected&&!anchor.hidden) {
        if(anchor.tagName==="FORM")anchor.append(node);
        else anchor.after(node);
      }else {
        const fallback=fallbacks.find(parent=>parent.isConnected);
        // Lists can rebuild/remove an edited row after a successful save.
        // Keep the result in that feature, with a visible notice if needed.
        const existing=feedbackFallbacks.get(fallback);
        if(existing?.isConnected)node=existing;
        else {
          const heading=fallback?.querySelector("summary,h2,h3");
          if(heading)heading.after(node);else fallback?.prepend(node);
          if(fallback)feedbackFallbacks.set(fallback,node);
        }
      }
    }
    node.classList.add("action-feedback");
    return node;
  }
  const writer={
    get textContent(){return node?.textContent||"";},
    set textContent(value){
      const text=String(value||"");
      const target=ensureNode();
      target.textContent=text;
      target.dataset.tone=/not finalized|not saved|could not|couldn't|failed|invalid|unavailable|cannot|denied|error/i.test(text)?"error":/saved|finalized|created|updated|ready|copied|deleted|removed|refreshed|closed|reopened/i.test(text)?"success":"info";
      if(!text)return;
      // Do not surface an old request over a different screen. Its inline
      // result remains in its owning section for the next visit.
      if(region?.hidden)return;
      clearFeedbackNotice();
      const rect=target.getBoundingClientRect();
      if(target.isConnected&&rect.top>=0&&rect.bottom<=innerHeight-80)return;
      // A long preview or rebuilt list can move the inline result offscreen.
      // A dismissible copy lets the user read it without scrolling the page.
      if(!feedbackNotice){
        feedbackNotice=document.createElement("div");
        feedbackNotice.className="feedback-notice";
        const message=document.createElement("span");
        message.setAttribute("aria-hidden","true"); // Inline live region announces once.
        const dismiss=document.createElement("button");
        dismiss.type="button";dismiss.className="secondary";dismiss.textContent="×";
        dismiss.setAttribute("aria-label","Dismiss message");
        dismiss.addEventListener("click",clearFeedbackNotice);
        feedbackNotice.append(message,dismiss);document.body.append(feedbackNotice);
      }
      feedbackNotice.firstElementChild.textContent=text;
      feedbackNotice.dataset.tone=target.dataset.tone;
      feedbackNotice.hidden=false;
      feedbackNoticeTimer=setTimeout(clearFeedbackNotice,target.dataset.tone==="error"?12000:8000);
    }
  };
  feedbackWriters.set(anchor,writer);
  return writer;
}
window.addEventListener("hashchange",clearFeedbackNotice);
