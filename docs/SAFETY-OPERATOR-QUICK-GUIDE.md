# INGPCE Safety Monitoring — Operator Guide

> **Audience:** Safety professionals and shift operators with no coding experience  
> **Purpose:** Start the prepared application, monitor cameras, and review detected incidents  
> **Document owner:** [Application owner — fill in]  
> **Site / version:** [Site and deployed version — fill in]  
> **Support contact:** [IT / application support — fill in]

## 1. What this application does

INGPCE shows configured camera feeds and highlights possible PPE or
phone-related safety events. It saves incident images and, when configured,
video clips. The dashboard also provides camera health, incident trends, and
alert delivery tools.

**Important:** The system reports possible events, not proven violations. A
camera angle, blocked view, lighting, or model error can cause an incorrect or
missed alert. Always review the evidence and follow your site's normal safety
procedure. Do not use a detection, face suggestion, or estimated speed by
itself to make disciplinary, identity, or enforcement decisions.

## 2. Before you start

Your IT/application support team should prepare the computer and application
before handing it to you. You should receive:

- The name of the computer where the application runs.
- The approved dashboard address. If you are using the application on its
  host computer, this is commonly `http://127.0.0.1:8080`. If you are using a
  different computer, use only the address supplied by IT.
- Your own username and password, if sign-in is enabled.
- The list of cameras and areas that should be visible.
- The support contact and instructions for whether the app is started
  automatically or manually.
- The approved process for incident review and alert dispatch at your site.

Do not edit configuration files, install software, change administrator
settings, share passwords, or use an unapproved public link. If the computer
or application has not been prepared, ask IT to complete the handover first.

## 3. Start and open the application

### If IT has configured automatic startup

Open the dashboard using the approved address or shortcut. Do not start a
second copy of the application unless IT asks you to.

### If IT has asked you to start it manually

1. Sign in to the designated application computer using your normal Windows
   account.
2. Open the **INGPCE application folder** that IT provided.
3. Double-click **`run_server.bat`**.
4. Leave the black server window open. It is running the application; closing
   it may stop the dashboard.
5. Open a browser on that same computer and go to
   **`http://127.0.0.1:8080`**, unless IT gave you a different approved
   address.
6. If the sign-in screen appears, enter your own operations username/email and
   password, then choose **Sign in**.

If a window says that setup or a Python environment is missing, stop there and
contact IT. Do not follow the setup prompt yourself.

### Stop the application

If you started the black server window manually and have been asked to stop
the application, select that window, press **Ctrl+C**, and wait for it to
finish. If the application is managed by IT or starts automatically, ask IT
before stopping or restarting it.

## 4. Check that the system is ready

When the dashboard opens:

1. Wait for the header to finish loading.
2. Check the camera count and the **Live Wall**.
3. Confirm that the expected cameras show a live image.
4. If a camera is blank, stopped, or repeatedly reconnecting, treat that as a
   camera/system issue—not as evidence that the area is safe.
5. Use the **Camera Health** view to check which feeds are online and whether
   recent connectivity history exists.

The dashboard's counters are system counts, not a human-verified safety
assessment. “No incidents” does not guarantee that all cameras were operating
or that no unsafe condition occurred.

## 5. Main dashboard areas

Names may appear in capital letters in the dashboard.

| Dashboard area | What to use it for |
|---|---|
| **Live Wall / Playback** | View live cameras or request recorded video from a supported NVR |
| **Incidents** | Review possible events, inspect images/clips, and record a review decision |
| **Analytics / Camera Health** | See incident patterns and camera availability |
| **Alert Delivery / Rules** | For authorized managers/admins: review delivery and send eligible evidence |
| **Face Matches** | Optional, restricted feature; only use if your site has formally approved it |
| **Settings** | Administrator-only. Operators should not change application settings |

Some areas are hidden if your account does not have permission. If you believe
you need access, ask your supervisor or application administrator; do not use
another person's account.

## 6. View live cameras

1. Choose **Live Wall / Playback**, then **Live Wall**.
2. Use **Area** to narrow the wall to a sector, or search for a camera by name.
3. Use **2 COL**, **3 COL**, or **4 COL** to change the number of columns.
4. Select a camera tile to inspect it more closely.
5. If one tile appears disconnected, try its reconnect control once. Use
   **Reconnect All** only when several feeds need a retry.
6. If it remains unavailable, record the camera name and time and contact IT or
   the camera owner.

Repeated reconnect attempts will not fix a network, camera, or NVR outage. Do
not change camera URLs or credentials unless you are an authorized camera
administrator.

## 7. Review an incident

1. Choose **Incidents**.
2. Use the camera, area, event type, and date-range filters to find the event.
   The initial date range is the **last 2 days**; choose **All History** if
   needed.
3. Choose **Refresh** after changing the filters.
4. Select an incident image to open its details.
5. Check the displayed camera, area, detection time, event type, and image.
   Compare the image with the actual site rule and your normal review process.
6. If a clean image or video clip is available, open it as additional context.
   Video availability depends on camera/NVR recording and evidence capture.
7. When you have made a decision under your site's review policy, choose:
   - **Confirm** when the evidence supports the detected event.
   - **False positive** when the evidence shows the event is not valid.
8. Return to the gallery and continue with the next event.

The current review controls save a decision but do not provide a comment box.
If your site requires contextual notes, record them in the approved incident
management system and include the INGPCE incident number.

The incident gallery displays events with an available snapshot. If an event
you expect is not shown, check the selected filters and date range. If it is
still missing, report the camera and approximate time to support.

### Evidence handling

- Treat incident images and clips as restricted operational information.
- Download or share evidence only when your site policy allows it.
- Store evidence only in approved locations; do not upload it to public sites
  or general-purpose chat.
- The **Delete incident** action permanently removes a record and should not
  be used as a substitute for marking a false positive.
- **Clean Older Than 2 Days** is an administrator-only permanent cleanup
  function. Do not use it to manage routine review.

## 8. Play recorded video

Playback is available only when the camera/NVR supports the configured
recording interface.

1. In **Live Wall / Playback**, choose **Playback**.
2. Select a camera.
3. Select the recording start date and time. The time is the **camera/NVR's
   local clock**, which may differ from your computer's clock.
4. Select a duration (5, 10, 30, 60, or 120 minutes).
5. Choose **Play Recording**.
6. Choose **Stop** when finished.

If playback does not start, verify the camera, time zone/clock, and that a
recording exists for the selected time. Try another known recording. If it
still fails, report it to IT/camera support.

## 9. Alerts and escalation

The application can send configured alerts automatically. Authorized
managers/admins may also see **Alert Delivery**, where eligible evidence can be
selected for manual dispatch.

- Use the approved escalation procedure; an email delivery does not replace
  an emergency call, radio procedure, or direct supervisor notification.
- Before sending, review the selected images and the recipients displayed by
  your deployment. If recipients are unexpected or unclear, do not send; ask
  the alert-flow owner.
- The current Power Automate incident delivery supports missing-helmet and
  phone-detected events. Availability depends on server configuration and the
  site's alert setup.
- After a manual send, check the delivery result/history. If it fails, do not
  repeatedly resend without checking with the alert owner.
- Do not change alert rules, recipients, or webhook settings unless you are
  authorized to do so.

## 10. Analytics and camera health

### Analytics

Choose **Analytics / Camera Health**, then **Analytics**. Select a reporting
period and choose **Refresh**. Charts summarize recorded incidents by time,
event type, area, or camera. These are operational summaries, not verified
incident findings; account for camera outages, filters, and review status
before drawing conclusions.

### Camera Health

Choose **Analytics / Camera Health**, then **Camera Health**. Select a time
range, camera, or area and choose **Refresh**. The timeline distinguishes
online, partial, offline, and unknown periods. **Unknown** means there is not
enough status history to make an availability statement; it does not mean
online.

If one or more enabled cameras need attention, report the affected cameras and
time period to IT/camera support. Avoid interpreting incident counts during a
camera outage as a complete picture of site activity.

## 11. Optional face matching and speed estimates

These features may not be enabled at your site.

- **Face Matches:** If the tab is visible, follow your site's approved
  biometric-data procedure. A suggested candidate is not an identity
  confirmation. A qualified human reviewer must independently verify it.
  Do not use a candidate for discipline or enforcement on its own. If you are
  unsure whether your use is approved, do not open or export the data; contact
  your privacy/safety lead.
- **Vehicle speed:** Treat a displayed value as an estimate only. Do not use it
  as a certified speed measurement or enforcement record.

## 12. Common problems

| What you see | What to do |
|---|---|
| Dashboard does not open | Confirm you are using the address from IT and, for the local address, the correct host computer. Ask IT whether the service is running. |
| Sign-in fails | Check the username/email and password once; use the approved password-reset/contact process. Do not share credentials. |
| Black or reconnecting camera tile | Try reconnect once; note camera name/time; contact camera/IT support if it persists. |
| “No incidents found” | Check the camera, area, event type, and date filters; refresh; use All History if needed. |
| Incident image or clip will not open | Try refresh and check whether other evidence opens. Report the incident number to support. |
| Playback says unavailable | Check that the recording time is in the NVR's local time and that a recording exists. |
| Alert send failed | Note the time and result; contact the alert-flow owner. Do not paste a webhook URL or credentials into the report. |
| Settings or administrator page is missing | This is expected for non-admin accounts. Ask your administrator if access is needed. |
| Server window shows an error or closes | Do not edit files or retry setup commands. Record the non-sensitive error text and contact IT. |

When reporting a problem, include the date/time, camera name or incident
number, what you clicked, and the message shown. Do not send passwords,
webhook links, employee photos, or unredacted sensitive evidence through an
unapproved channel.

## 13. Suggested shift routine

### At the start of the shift

- [ ] Open the approved dashboard and sign in with your own account.
- [ ] Check the Live Wall and note any camera feeds that are unavailable.
- [ ] Check Camera Health for unknown/offline periods that affect coverage.
- [ ] Review the incident queue using the site's assigned time range.
- [ ] Confirm the escalation contact for urgent events.

### During the shift

- [ ] Review incidents using the site's procedure and record Confirm/False
  positive only when you have reviewed the evidence.
- [ ] Use playback when additional context is needed and available.
- [ ] Escalate urgent safety concerns through the normal site response channel;
  do not rely only on an application alert.
- [ ] Report persistent camera, evidence, sign-in, or alert problems.

### At the end of the shift

- [ ] Complete any pending reviews or hand over incident numbers to the next
  shift.
- [ ] Note camera outages and unresolved system problems.
- [ ] Sign out when finished if the dashboard provides a **Sign out** button.
- [ ] Leave the application running unless your site procedure asks you to
  stop it.

## 14. Site-specific handover fields

Have the site/application owner complete this section before the guide is
issued:

| Item | Site value |
|---|---|
| Approved dashboard address | [Fill in; do not use an unapproved temporary public URL] |
| Application host / room | [Fill in] |
| Automatic or manual startup | [Fill in] |
| Camera/area inventory | [Link to approved inventory] |
| Safety incident review procedure | [Link to approved procedure] |
| Urgent escalation contact | [Fill in] |
| IT/application support contact | [Fill in] |
| Camera/NVR support contact | [Fill in] |
| Alert-flow owner | [Fill in] |
| Evidence handling and retention policy | [Link to approved policy] |
| Face matching approved at this site? | [Yes / No / Not enabled; identify approval] |

**Operator acknowledgement (if required by site process):**

- Name: ______________________________
- Role / site: _________________________
- Date: _______________________________
- Training completed by: _______________
