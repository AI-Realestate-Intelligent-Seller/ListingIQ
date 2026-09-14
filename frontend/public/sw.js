self.addEventListener("push", function (event) {

    let data = {};

    if (event.data) {
        try {
            data = event.data.json();
        } catch {
            data = {
                title: "ListingIQ",
                body: event.data.text(),
            };
        }
    }

    const title =
        data.title || "ListingIQ";

    const options = {
        body:
            data.body ||
            "You have a new notification.",

        icon:
            "/icons/icon-192.png",

        data: {
            url:
                data.url || "/calendar",
        }
    };

    event.waitUntil(
        self.registration.showNotification(
            title,
            options
        )
    );
});