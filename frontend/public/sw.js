let activeConversationId = null;


/*
 * Frontend tells the Service Worker which
 * conversation is currently open.
 */
self.addEventListener(
    "message",
    function (event) {

        const data =
            event.data || {};

        if (
            data.type ===
            "ACTIVE_CONVERSATION"
        ) {

            activeConversationId =
                data.conversation_id ??
                null;

            console.log(
                "[SW] Active conversation:",
                activeConversationId
            );
        }
    }
);


self.addEventListener(
    "push",
    function (event) {
        // Read permission for each delivery, including queued push messages.
        if (self.Notification?.permission !== "granted") {
            return;
        }

        let data = {};

        if (event.data) {
            try {
                data =
                    event.data.json();
            } catch {
                data = {
                    title:
                        "ListingIQ",
                    body:
                        event.data.text(),
                };
            }
        }


        const conversationId =
            data.conversation_id ??
            null;


        const title =
            data.title ||
            "ListingIQ";


        const options = {

            body:
                data.body ||
                "You have a new notification.",

            icon:
                "/icons/icon-192.png",

            // Grouped replies replace one popup per conversation, and still alert.
            ...(conversationId !== null
                ? { tag: `conversation-${conversationId}`, renotify: true }
                : {}),

            data: {
                action: data.action,

                url:
                    data.url ||
                    "/",

                conversation_id:
                    conversationId,
            },
        };


        event.waitUntil((async () => {
            /*
             * Suppress the popup only while the user is actually looking at
             * this conversation. A conversation left open in a background
             * tab, a minimised window or a backgrounded phone browser still
             * gets a system notification, like WhatsApp Web.
             */
            if (
                conversationId !== null &&
                activeConversationId !== null &&
                Number(conversationId) === Number(activeConversationId)
            ) {
                const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
                if (windows.some((client) => client.visibilityState === "visible" && client.focused)) {
                    console.log("[SW] Push suppressed for active conversation:", conversationId);
                    return;
                }
            }

            await self.registration.showNotification(title, options);
        })());
    }
);

// Push clicks use the same dashboard target as the notification bell.
self.addEventListener("notificationclick", function (event) {
    event.notification.close();
    const data = event.notification.data || {};
    event.waitUntil((async () => {
        let target;
        try {
            target = new URL(data.url || "/dashboard", self.location.origin);
        } catch {
            target = new URL("/dashboard", self.location.origin);
        }
        if (target.origin !== self.location.origin) target = new URL("/dashboard", self.location.origin);
        const action = data.action;
        const views = ["overview", "directory", "assignments", "leads", "campaigns", "followups", "calendar"];
        if (action && views.includes(action.view)) {
            target = new URL("/dashboard", self.location.origin);
            target.searchParams.set("view", action.view);
            for (const key of ["conversation_id", "booking_id", "lead_id", "campaign_id"]) {
                if (Number.isSafeInteger(Number(action[key])) && Number(action[key]) > 0) {
                    target.searchParams.set(key, String(action[key]));
                }
            }
        } else if (data.conversation_id) {
            target = new URL(`/dashboard?view=followups&conversation_id=${encodeURIComponent(data.conversation_id)}`, self.location.origin);
        } else if (!/^\/dashboard(?:\/|$)/.test(target.pathname)) {
            const booking = target.pathname.match(/^\/calendar\/bookings\/(\d+)$/);
            const view = target.pathname === "/assignments" ? "leads" :
                (booking || ["/calendar", "/agent"].includes(target.pathname)) ? "calendar" : "overview";
            target = new URL(`/dashboard?view=${view}`, self.location.origin);
            if (booking) target.searchParams.set("booking_id", booking[1]);
        }
        const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
        const dashboard = windows.find((client) => {
            const url = new URL(client.url);
            return url.origin === self.location.origin && /^\/dashboard(?:\/|$)/.test(url.pathname);
        });
        if (dashboard) {
            await dashboard.focus();
            const handled = await new Promise((resolve) => {
                const channel = new MessageChannel();
                const finish = (value) => {
                    clearTimeout(timer);
                    channel.port1.close();
                    resolve(value);
                };
                const timer = setTimeout(() => finish(false), 1000);
                channel.port1.onmessage = (message) => finish(message.data?.handled === true);
                dashboard.postMessage({
                    type: "NOTIFICATION_NAVIGATE",
                    action,
                    url: target.pathname + target.search,
                }, [channel.port2]);
            });
            // A loading tab may not have attached its navigation listener yet.
            if (!handled) await dashboard.navigate(target.href);
        } else {
            await self.clients.openWindow(target.href);
        }
    })());
});
