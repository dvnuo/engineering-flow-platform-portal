---
icon: scan-eye
---
## What this is for

The model an assistant chats with cannot see images. This connector is how
the assistant reads them: a screenshot of an error, a photo of a whiteboard,
a diagram, a chart. It hands the image file to the `inspect-image` tool,
which sends it to AI Platform and brings back what the picture says and
shows.

It works whichever model provider the assistant chats with, and where there
is no browser to help: background tasks, delegated work and sub-assistants
read images this way too.

## What to enter

- **Username**, **Password** and **Usercase** are an AI Platform account.
  All three are needed while the connector is on. The account is separate
  from the one in the Model provider connector: if you chat through AI
  Platform and do not follow your group's default here, enter it in both.
- **Model** is the AI Platform model that reads the images, not the model
  you chat with. Keep it unless your administrator names another one.

Most members fill in nothing. When an administrator has set a shared account
in Default connectors, the panel says it follows your group's Default
connectors and images are already read. Choose Customize only to use an
account of your own.

## What happens to an image

1. With this connector on, the assistant reads the image with
   `inspect-image`.
2. Without it, in a chat with the Local bridge switched on, the assistant
   offers to analyze the file through Productivity Suite in your own
   browser, as it does for documents.
3. With neither, the assistant tells you which files it could not read. It
   does not guess.

Old Office files (`.doc`, `.dot`, `.xls`) are not images: they always go
through Productivity Suite.

## If it stops working

Use Test image analysis on the panel: it signs in to AI Platform with the
account and sends one tiny image.

- A failed sign-in means the username, password or usercase is wrong, or
  the password has expired.
- A message that the AI Platform host is not configured is for your
  administrator: the Portal's deployment settings say where AI Platform
  lives.
- When the test passes but an assistant still cannot read images, restart
  the assistant: connector changes reach a running assistant when it
  restarts.

When `inspect-image` fails during a chat and the Local bridge is on, the
assistant falls back to Productivity Suite and says so.
