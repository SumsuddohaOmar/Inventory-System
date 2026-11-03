/* First-time administrator setup and sign-in. No embedded/default password.
   Server cookies are HttpOnly. Only the public account profile is cached for
   previously authenticated offline use; reconnecting rechecks server rights. */
"use strict";
let needsSetup = false;
let pinUnlocked = false;

async function sessionGate() {
  let session;
  try {
    const response = await fetch("/api/session", {signal:AbortSignal.timeout(12000)});
    if (!response.ok) throw new Error("Account status is unavailable.");
    session = await response.json();
    needsSetup = !session.configured && !session.operator_setup;
    currentUser = session.user;
    // Failure to save a local profile must never switch an online user to a
    // different account's old offline profile.
    if (currentUser) {
      try { await localPut("meta",{id:"last-user",value:currentUser}); }
      catch { document.querySelector("#sync-status").textContent="Browser storage unavailable. Offline sign-in cannot be prepared."; }
    }
  } catch {
    currentUser = (await localGet("meta","last-user"))?.value || null;
  }
  document.querySelector("#auth-title").textContent = needsSetup ? "Set up your administrator account" : "Sign in to your inventory";
  document.querySelector("#auth-help").textContent = needsSetup ? "Choose your own username and a password of at least 12 characters. Your saved inventory is preserved." : "Use your account password. Trusted-device sessions can be remembered.";
  document.querySelector("#auth-submit").textContent = needsSetup ? "Create administrator & continue" : "Sign in";
  document.querySelector("#auth-panel").hidden = !!currentUser;
  document.querySelector("#main").hidden = !currentUser;
  document.querySelector("#logout").hidden = !currentUser;
  if(currentUser) document.querySelector("#account-name").textContent = currentUser.username;
  if(currentUser && !pinUnlocked && await localGet("meta",`pin:${currentUser.id}`)) {
    document.querySelector("#pin-panel").hidden=false;
    document.querySelector("#auth-panel").hidden=true;
    document.querySelector("#main").hidden=true;
    return false;
  }
  document.querySelector("#pin-panel").hidden=true;
  return !!currentUser;
}

document.querySelector("#auth-form").addEventListener("submit", async(event)=>{
  event.preventDefault();
  const form=event.currentTarget, button=document.querySelector("#auth-submit");
  button.disabled=true;
  try {
    const response=await fetch(needsSetup?"/api/setup":"/api/session",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({username:form.elements.username.value,password:form.elements.password.value,remember:form.elements.remember.checked})});
    const result=await response.json();
    if(!response.ok) throw new Error(typeof result.detail==="string"?result.detail:"Please check the account details.");
    form.elements.password.value="";
    // Full account authentication is the recovery path for a forgotten PIN.
    pinUnlocked=true;
    if(await sessionGate()) { await initialize(); await loadWorkspace(); }
  } catch(error) { document.querySelector("#auth-error").textContent=error.message; }
  finally {button.disabled=false;}
});

async function pinHash(pin,salt) {
  const material=await crypto.subtle.importKey("raw",new TextEncoder().encode(pin),"PBKDF2",false,["deriveBits"]);
  const bytes=await crypto.subtle.deriveBits({name:"PBKDF2",salt:new Uint8Array(salt),iterations:250000,hash:"SHA-256"},material,256);
  return [...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,"0")).join("");
}
document.querySelector("#pin-save").addEventListener("click",async()=>{
  const pin=document.querySelector("#new-pin").value;
  if(!/^\d{4,12}$/.test(pin)){status.textContent="Use a PIN of 4–12 digits.";return;}
  const salt=[...crypto.getRandomValues(new Uint8Array(16))];
  await localPut("meta",{id:`pin:${currentUser.id}`,salt,hash:await pinHash(pin,salt),failures:0,blockedUntil:0});
  document.querySelector("#new-pin").value="";pinUnlocked=true;status.textContent="App PIN set for this device.";
});
document.querySelector("#pin-unlock-form").addEventListener("submit",async(event)=>{
  event.preventDefault();const saved=await localGet("meta",`pin:${currentUser.id}`);
  if(saved.blockedUntil>Date.now()){document.querySelector("#pin-error").textContent="Too many attempts. Try again in five minutes or sign in with your account password.";return;}
  const hash=await pinHash(document.querySelector("#unlock-pin").value,saved.salt);
  let difference=0;for(let index=0;index<hash.length;index++)difference|=hash.charCodeAt(index)^saved.hash.charCodeAt(index);
  document.querySelector("#unlock-pin").value="";
  if(difference){saved.failures=(saved.failures||0)+1;if(saved.failures>=5){saved.blockedUntil=Date.now()+300000;saved.failures=0;}await localPut("meta",saved);document.querySelector("#pin-error").textContent="Incorrect PIN.";return;}
  saved.failures=0;saved.blockedUntil=0;await localPut("meta",saved);pinUnlocked=true;document.querySelector("#pin-error").textContent="";
  if(await sessionGate()){await initialize();await loadWorkspace();}
});
document.querySelector("#pin-use-password").addEventListener("click",()=>{document.querySelector("#pin-panel").hidden=true;document.querySelector("#auth-panel").hidden=false;});
document.querySelector("#pin-lock").addEventListener("click",async()=>{if(!await localGet("meta",`pin:${currentUser.id}`)){status.textContent="Set a PIN first.";return;}pinUnlocked=false;await sessionGate();});
document.querySelector("#pin-remove").addEventListener("click",async()=>{
  const password=document.querySelector("#pin-account-password");
  const response=await fetch("/api/session",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:currentUser.username,password:password.value})});
  password.value="";
  if(!response.ok){status.textContent="Password verification failed. PIN kept.";return;}
  await localDelete("meta",`pin:${currentUser.id}`);pinUnlocked=true;status.textContent="App PIN removed on this device.";
});
let lastActivity=Date.now();for(const name of ["pointerdown","keydown"])document.addEventListener(name,()=>{lastActivity=Date.now();},{passive:true});
setInterval(async()=>{if(currentUser&&pinUnlocked&&Date.now()-lastActivity>300000&&await localGet("meta",`pin:${currentUser.id}`)){pinUnlocked=false;await sessionGate();}},30000);

document.querySelector("#logout").addEventListener("click",async()=>{
  await fetch("/api/logout",{method:"POST"});
  await localDelete("meta","last-user");
  currentUser=null;
  // Pending operations stay partitioned under their original account, never
  // reassigned to whoever signs in next. Signing out does not discard work.
  location.reload();
});
