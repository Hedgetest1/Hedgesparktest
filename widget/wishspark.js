(function () {
    const API_URL = "http://147.93.127.248:8000/track-event";

    function getVisitorId() {
        let visitorId = localStorage.getItem("wishspark_visitor_id");

        if (!visitorId) {
            visitorId = "ws_" + Math.random().toString(36).substring(2) + Date.now();
            localStorage.setItem("wishspark_visitor_id", visitorId);
        }

        return visitorId;
    }

    function getSessionId() {
        let sessionId = sessionStorage.getItem("wishspark_session_id");

        if (!sessionId) {
            sessionId = "sess_" + Math.random().toString(36).substring(2) + Date.now();
            sessionStorage.setItem("wishspark_session_id", sessionId);
        }

        return sessionId;
    }

    function detectSourceType() {
        const referrer = document.referrer || "";

        if (!referrer) {
            return "direct";
        }

        const lower = referrer.toLowerCase();

        if (lower.includes("instagram") || lower.includes("facebook") || lower.includes("tiktok") || lower.includes("x.com") || lower.includes("twitter") || lower.includes("pinterest")) {
            return "social";
        }

        if (lower.includes("google") || lower.includes("bing") || lower.includes("yahoo") || lower.includes("duckduckgo")) {
            return "search";
        }

        return "referral";
    }

    function getReferrer() {
        return document.referrer || "";
    }

    function getPageUrl() {
        return window.location.href;
    }

    function getPageTitle() {
        return document.title;
    }

    function getOccurredAt() {
        return new Date().toISOString();
    }

    let maxScrollDepth = 0;
    let pageStartTime = Date.now();

    function updateScrollDepth() {
        const scrollTop = window.scrollY;
        const docHeight = document.documentElement.scrollHeight - window.innerHeight;

        if (docHeight <= 0) {
            maxScrollDepth = 100;
            return;
        }

        const currentDepth = Math.round((scrollTop / docHeight) * 100);

        if (currentDepth > maxScrollDepth) {
            maxScrollDepth = currentDepth;
        }
    }

    async function trackEvent(type, data = {}, extras = {}) {
        const visitorId = getVisitorId();
        const sessionId = getSessionId();

        try {
            const response = await fetch(API_URL, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    visitor_id: visitorId,
                    session_id: sessionId,
                    event_type: type,
                    page_url: getPageUrl(),
                    page_title: getPageTitle(),
                    source_type: detectSourceType(),
                    referrer: getReferrer(),
                    dwell_seconds: extras.dwell_seconds ?? null,
                    scroll_depth: extras.scroll_depth ?? null,
                    event_data: data,
                    occurred_at: getOccurredAt()
                })
            });

            const result = await response.json();
            console.log("WishSpark event sent:", result);
            return result;
        } catch (err) {
            console.error("WishSpark tracking error", err);
        }
    }

    function trackProductView() {
        const product = {
            url: window.location.href,
            title: document.title
        };

        trackEvent("product_view", product);
    }

    function trackPageDwell() {
        const dwellSeconds = Math.round((Date.now() - pageStartTime) / 1000);

        trackEvent(
            "product_dwell",
            {
                url: window.location.href,
                title: document.title
            },
            {
                dwell_seconds: dwellSeconds,
                scroll_depth: maxScrollDepth
            }
        );
    }

    function createWishlistButton() {
        const btn = document.createElement("button");

        btn.innerText = "♡ Add to Smart Wishlist";
        btn.style.position = "fixed";
        btn.style.bottom = "20px";
        btn.style.right = "20px";
        btn.style.padding = "12px 18px";
        btn.style.background = "black";
        btn.style.color = "white";
        btn.style.border = "none";
        btn.style.borderRadius = "8px";
        btn.style.cursor = "pointer";
        btn.style.zIndex = "9999";

        btn.onclick = async function () {
            const product = {
                url: window.location.href,
                title: document.title
            };

            const result = await trackEvent(
                "wishlist_add",
                product,
                {
                    dwell_seconds: Math.round((Date.now() - pageStartTime) / 1000),
                    scroll_depth: maxScrollDepth
                }
            );

            alert("Wishlist response: " + JSON.stringify(result));
        };

        document.body.appendChild(btn);
    }

    window.addEventListener("scroll", updateScrollDepth);

    window.addEventListener("beforeunload", function () {
        trackPageDwell();
    });

    document.addEventListener("DOMContentLoaded", function () {
        trackProductView();
        createWishlistButton();
        updateScrollDepth();
    });
})();
