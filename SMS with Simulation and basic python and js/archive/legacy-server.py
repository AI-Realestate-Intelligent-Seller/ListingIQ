import express from 'express';

const app = express();
app.use(express.json());

app.post('/webhooks', (req, res) => {
  const { data } = req.body;

  if (data.event_type === 'message.received') {
    const { payload } = data;
    console.log(`From: ${payload.from.phone_number}`);
    console.log(`Text: ${payload.text}`);
    console.log(`Type: ${payload.type}`); // SMS or MMS

    if (payload.media?.length > 0) {
      console.log(`Media attachments: ${payload.media.length}`);
      payload.media.forEach(m => console.log(`  ${m.content_type}: ${m.url}`));
    }
  }

  res.sendStatus(200);
});

app.listen(5000, () => console.log('Webhook server running on port 5000'));