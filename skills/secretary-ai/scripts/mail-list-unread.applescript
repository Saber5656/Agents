-- secretary-ai: Apple Mail 未読メール一覧取得
-- Usage: osascript scripts/mail-list-unread.applescript [account_name] [limit]
--   account_name: 対象アカウント名（省略時は全アカウント）
--   limit:        最大件数（省略時は 20）
-- Output: TSV (date<TAB>sender<TAB>subject<TAB>account<TAB>mailbox<TAB>messageId)
-- 設計方針:
--   - 本文は取得しない（量と速度のため）。本文は mail-get-message.applescript で個別取得
--   - 失敗時は stderr にエラーを出して exit 1
--   - インジェクション対策: 引数は文字列リテラルとしてのみ扱い、AppleScript 文字列に補間しない

on run argv
	set accountFilter to ""
	set maxCount to 20
	if (count of argv) ≥ 1 then set accountFilter to item 1 of argv
	if (count of argv) ≥ 2 then
		try
			set maxCount to (item 2 of argv) as integer
		on error
			set maxCount to 20
		end try
	end if

	set output to ""
	set collected to 0

	tell application "Mail"
		set targetAccounts to every account
		repeat with acc in targetAccounts
			if accountFilter is "" or (name of acc as string) is accountFilter then
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
						set unreadMsgs to (messages of inboxMb whose read status is false)
						repeat with msg in unreadMsgs
							if collected ≥ maxCount then exit repeat
							try
								set msgDate to (date received of msg) as string
								set msgSubject to subject of msg
								set msgSender to sender of msg
								set msgId to message id of msg
								set msgAccount to name of acc as string
								set msgMailbox to name of inboxMb as string

								-- TAB と改行を除去してフィールド境界を保護
								set msgSubject to my sanitize(msgSubject)
								set msgSender to my sanitize(msgSender)
								set msgId to my sanitize(msgId)

								set lineRecord to msgDate & tab & msgSender & tab & msgSubject & tab & msgAccount & tab & msgMailbox & tab & msgId & linefeed
								set output to output & lineRecord
								set collected to collected + 1
							on error errMsg
								-- 個別メール取得失敗はスキップして継続
							end try
						end repeat
					end try
				end if
			end if
			if collected ≥ maxCount then exit repeat
		end repeat
	end tell

	return output
end run

on sanitize(s)
	set s to s as string
	set AppleScript's text item delimiters to tab
	set parts to text items of s
	set AppleScript's text item delimiters to " "
	set s to parts as string
	set AppleScript's text item delimiters to linefeed
	set parts to text items of s
	set AppleScript's text item delimiters to " "
	set s to parts as string
	set AppleScript's text item delimiters to return
	set parts to text items of s
	set AppleScript's text item delimiters to " "
	set s to parts as string
	set AppleScript's text item delimiters to ""
	return s
end sanitize
