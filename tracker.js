(function(){

const API="http://147.93.127.248:8000/track"

const visitorId=localStorage.getItem("ws_visitor") || crypto.randomUUID()
localStorage.setItem("ws_visitor",visitorId)

let startTime=Date.now()
let maxScroll=0

function send(event,data={}){
fetch(API,{
method:"POST",
headers:{
"Content-Type":"application/json"
},
body:JSON.stringify({
visitor_id:visitorId,
event:event,
url:window.location.pathname,
timestamp:Date.now(),
...data
})
}).catch(()=>{})
}

send("page_view")

window.addEventListener("scroll",()=>{

const scroll=Math.round(
(window.scrollY+window.innerHeight)/document.body.scrollHeight*100
)

if(scroll>maxScroll){
maxScroll=scroll
}

})

document.addEventListener("click",(e)=>{

const el=e.target.closest("button,a")
if(!el)return

send("click",{
text:el.innerText || "unknown"
})

})

window.addEventListener("beforeunload",()=>{

const dwell=Math.round((Date.now()-startTime)/1000)

send("page_leave",{
dwell_seconds:dwell,
max_scroll_depth:maxScroll
})

})

})()
