-- secretary-ai: Apple Mail 返信ドラフト作成
-- Usage: osascript scripts/mail-create-draft.applescript <messageId> <bodyFilePath>
--   messageId:    元メールの message-id（返信先の特定）
--   bodyFilePath: 返信本文を書いた一時ファイルのパス（POSIX path）
-- 設計方針:
--   - 送信は絶対にしない。下書きとして保存するのみ
--   - 本文は引数ではなくファイル経由で渡す（巨大本文・特殊文字対策）
--   - reply の visible:true で Mail.app にドラフトウィンドウとして表示

on run argv
	if (count of argv) < 2 then
		error "messageId and bodyFilePath are required" number 1
	end if
	set targetId to item 1 of argv as string
	set bodyPath to item 2 of argv as string

	-- 本文をファイルから読み込み
	set bodyText to ""
	try
		set fileRef to (POSIX file bodyPath) as alias
		set fileHandle to open for access fileRef
		set bodyText to (read fileHandle as «class utf8»)
		close access fileHandle
	on error errMsg
		try
			close access (POSIX file bodyPath)
		end try
		error "Failed to read body file: " & errMsg number 3
	end try

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
						exit repeat
					end if
				end try
			end if
		end repeat

		if found is missing value then
			error "Message not found: " & targetId number 2
		end if

		-- reply は新しい outgoing message を作成。visible:true でウィンドウを開く
		set replyMsg to reply found opening window yes with reply to all
		delay 0.5
		tell replyMsg
			set content to bodyText & linefeed & (content as string)
		end tell

		-- 明示的に送信しない。ドラフトはユーザー操作で保存される（Cmd+W で保存ダイアログ）
		-- もしくは: save replyMsg
		try
			save replyMsg
		end try

		return "draft_created"
	end tell
end run
