-- secretary-ai: Apple Mail メッセージ本文取得
-- Usage: osascript scripts/mail-get-message.applescript <messageId>
--   messageId: RFC822 message-id（mail-list-unread.applescript の出力から取得）
-- Output: ヘッダ行 + 空行 + 本文
--   Subject: ...
--   From: ...
--   To: ...
--   Date: ...
--   Account: ...
--   ---
--   <本文>

on run argv
	if (count of argv) < 1 then
		error "messageId is required" number 1
	end if
	set targetId to item 1 of argv as string

	tell application "Mail"
		set found to missing value
		repeat with acc in every account
			try
				set inboxMb to mailbox "INBOX" of acc
			on error
				try
					set inboxMb to mailbox "Inbox" of acc
				on error
					set inboxMb to missing value
				end try
			end try
			if inboxMb is not missing value then
				try
					set candidates to (messages of inboxMb whose message id is targetId)
					if (count of candidates) > 0 then
						set found to item 1 of candidates
						set foundAccount to name of acc as string
						exit repeat
					end if
				end try
			end if
		end repeat

		if found is missing value then
			error "Message not found: " & targetId number 2
		end if

		set msgSubject to subject of found
		set msgFrom to sender of found
		set msgTo to ""
		try
			set msgTo to address of to recipient 1 of found
		end try
		set msgDate to (date received of found) as string
		set msgBody to content of found

		set output to "Subject: " & msgSubject & linefeed
		set output to output & "From: " & msgFrom & linefeed
		set output to output & "To: " & msgTo & linefeed
		set output to output & "Date: " & msgDate & linefeed
		set output to output & "Account: " & foundAccount & linefeed
		set output to output & "---" & linefeed
		set output to output & msgBody
		return output
	end tell
end run
