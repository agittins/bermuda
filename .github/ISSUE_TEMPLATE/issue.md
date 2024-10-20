---
name: Issue
about: Create a report to help us improve
---

<!-- Before you open a new issue:

Thanks for taking the time to report your issue. I'd much rather hear about it
and help find a solution than for you to give up on what Bermuda might be able
to do for you.

Because Bermuda is starting to get a bit popular, and because things can be a bit
tricky to set up at first I'm finding it difficult to spend a lot of individual
time to help solve issues. So if you are able to follow through the instructions
here, it will hopefully help me find the source of the problem more quickly.

Many people bump into the same issues when they first start, so please take a
look through the Wiki where I try to address most of those FAQ's. Particularly
the "Troubleshooting" page at https://github.com/agittins/bermuda/wiki/Troubleshooting

Bear in mind the Wiki has sub-pages as well. These are usually listed in a menu
on the right-hand side, but might be collapsed into a sub-menu on mobile devices.

Also please search for existing issues, many common things like "my device bounces
between areas" have been brought up hundreds of times already, so the solution
might already be well covered for you there.


You can remove any of these comment blocks an other instructions from your issue
before submitting, but please take care to follow the instructions first!

The more you are able to do to gather the information I need to be able to help,
the quicker you might get a solution, and the more time (and energy) I can spend
on improving Bermuda for all!

-->

## Configuration

<!-- Ideally paste screenshots of the config dialogs, from Config -> Devices and Services -> Bermuda -> CONFIGURE,
then include the first diagnostic screen, the Global Options and Select Devices panels.
You can blur out the last 3 or 4 pairs of numbers in each MAC address for privacy reasons, but it's sometimes helpful
to leave the name and the first few digits (as it often gives a clue to the manufacturer and helps to have something
to reference in discussions).
-->

## Describe the bug

A clear and concise description of what the bug is.

## Diagnostics

In HA, Settings, Devices and Services, Bermuda, click the three-dots and choose "Download Diagnostics.
This will chug away for a bit then present you with a json file that you can upload here.

Attach your "Download Diagnostics" result by clicking the paperclip above:
![image](https://github.com/user-attachments/assets/7a3ce102-0b96-46c5-9289-e3253a6f2164)

If Bermuda has been running a long time (several days) the diagnostics might take a long time
to generate. You can try reloading Bermuda first (HA, Settings, Devices and Services, Bermuda,
three-dots-menu, Reload), then wait a few minutes, then try the download diagnostics.
