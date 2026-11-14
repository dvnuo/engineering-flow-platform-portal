---
title: Starting from a card
summary: Pick your own recent work instead of typing it, and refer to it with #.
group: Working
icon: mouse-pointer-click
order: 5
---
## Cards that list your work

A new chat opens with a few cards. A card that needs something from you (a
ticket, a pull request, a branch) opens a list of your own recent ones: issues
you viewed or that are assigned to you, pull requests waiting for your review,
your recent branches that have no pull request yet. Click a row and the
assistant starts. Type in the box to narrow the list or to search.

The box is always there. Paste a link or type a key and press Enter, exactly
as before; the list is only a shortcut.

Some cards ask for more than one thing, for example a story and the platforms
to run on. Fill in the fields and press **Continue**.

A card can carry a small count, such as **2 to review** or **1 failed**: work
that is waiting on you, from the same list the card opens.

## Where the list comes from

The Portal reads it with your own connector settings, the same ones your
assistant uses. Nothing is listed for a connector that is off: the card says
which one to set up under [Connectors](#/help/concepts), and the box still
works.

The list is always yours, whoever the connector signs in as. Many connectors
sign in with a shared account, and what that account did is not what you
did, so the Portal looks you up by your Portal username instead. **Recently
viewed** is then not offered: that history belongs to the shared account.

If your username is not found in that system, your recent work is not listed
and the box says so. Typing still searches.

What you pick is sent as a key or a link only. Titles and summaries are shown
to you and are not sent to the assistant.

## Branches for a pull request

For a pull request, the list starts with the branches checked out in this
assistant's workspace that have commits or changes of their own: work the
assistant did that nothing outside the workspace knows about yet.

Under them are your own recent branches: the ones you created or pushed to
lately in the repositories you work in, with the ones that have no pull
request yet first. A branch you pushed a minute ago is there.

Picking a branch fills in **Merge into** with that repository's default
branch. Change it when the pull request should go somewhere else.

## Referring to something while you type

Type `#` in the message box to get the same lists while writing a message of
your own: recent Jira issues, pull requests and Confluence pages. Use the
arrow keys and Enter to insert one, or keep typing to ignore the list. Enter
still sends your message.

## Values you used before

Each card remembers the last few values you gave it, in this browser, and
offers them under **Used before**.
