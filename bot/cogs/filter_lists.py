import logging
from typing import Optional

from discord import Colour, Embed
from discord.ext.commands import BadArgument, Cog, Context, IDConverter, group

from bot import constants
from bot.api import ResponseCodeError
from bot.bot import Bot
from bot.converters import ValidDiscordServerInvite, ValidFilterListType
from bot.pagination import LinePaginator
from bot.utils.checks import with_role_check

log = logging.getLogger(__name__)


class FilterLists(Cog):
    """Commands for blacklisting and whitelisting things."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _add_data(
            self,
            ctx: Context,
            allowed: bool,
            list_type: ValidFilterListType,
            content: str,
            comment: Optional[str] = None,
    ) -> None:
        """Add an item to a filterlist."""
        allow_type = "whitelist" if allowed else "blacklist"

        # If this is a server invite, we gotta validate it.
        if list_type == "GUILD_INVITE":
            log.trace(f"{content} is a guild invite, attempting to validate.")
            validator = ValidDiscordServerInvite()
            guild_data = await validator.convert(ctx, content)

            # If we make it this far without raising a BadArgument, the invite is
            # valid. Let's convert the content to an ID.
            log.trace(f"{content} validated as server invite. Converting to ID.")
            content = guild_data.get("id")

            # Unless the user has specified another comment, let's
            # use the server name as the comment so that the list
            # of guild IDs will be more easily readable when we
            # display it.
            if not comment:
                comment = guild_data.get("name")

        # Try to add the item to the database
        log.trace(f"Trying to add the {content} item to the {list_type} {allow_type}")
        payload = {
            'allowed': allowed,
            'type': list_type,
            'content': content,
            'comment': comment,
        }

        try:
            item = await self.bot.api_client.post(
                "bot/filter-lists",
                json=payload
            )
        except ResponseCodeError as e:
            if e.status == 400:
                await ctx.message.add_reaction("❌")
                log.debug(
                    f"{ctx.author} tried to add data to a {allow_type}, but the API returned 400, "
                    "probably because the request violated the UniqueConstraint."
                )
                raise BadArgument(
                    f"Unable to add the item to the {allow_type}. "
                    "The item probably already exists. Keep in mind that a "
                    "blacklist and a whitelist for the same item cannot co-exist, "
                    "and we do not permit any duplicates."
                )
            raise

        # Insert the item into the cache
        self.bot.insert_item_into_filter_list_cache(item)
        await ctx.message.add_reaction("✅")

    async def _delete_data(self, ctx: Context, allowed: bool, list_type: ValidFilterListType, content: str) -> None:
        """Remove an item from a filterlist."""
        item = None
        allow_type = "whitelist" if allowed else "blacklist"
        id_converter = IDConverter()

        # If this is a server invite, we need to convert it.
        if list_type == "GUILD_INVITE" and not id_converter._get_id_match(content):
            log.trace(f"{content} is a guild invite, attempting to validate.")
            validator = ValidDiscordServerInvite()
            guild_data = await validator.convert(ctx, content)

            # If we make it this far without raising a BadArgument, the invite is
            # valid. Let's convert the content to an ID.
            log.trace(f"{content} validated as server invite. Converting to ID.")
            content = guild_data.get("id")

        # Find the content and delete it.
        log.trace(f"Trying to delete the {content} item from the {list_type} {allow_type}")
        for allow_list in self.bot.filter_list_cache[f"{list_type}.{allowed}"]:
            if content == allow_list.get("content"):
                item = allow_list
                break

        if item is not None:
            await self.bot.api_client.delete(
                f"bot/filter-lists/{item.get('id')}"
            )
            self.bot.filter_list_cache[f"{list_type}.{allowed}"].remove(item)
            await ctx.message.add_reaction("✅")

    async def _list_all_data(self, ctx: Context, allowed: bool, list_type: ValidFilterListType) -> None:
        """Paginate and display all items in a filterlist."""
        allow_type = "whitelist" if allowed else "blacklist"
        result = self.bot.filter_list_cache[f"{list_type}.{allowed}"]

        # Build a list of lines we want to show in the paginator
        lines = []
        for item in result:
            line = f"• `{item.get('content')}`"

            if item.get("comment"):
                line += f" - {item.get('comment')}"

            lines.append(line)
        lines = sorted(lines)

        # Build the embed
        list_type_plural = list_type.lower().replace("_", " ").title() + "s"
        embed = Embed(
            title=f"{allow_type.title()}ed {list_type_plural} ({len(result)} total)",
            colour=Colour.blue()
        )
        log.trace(f"Trying to list {len(result)} items from the {list_type.lower()} {allow_type}")

        if result:
            await LinePaginator.paginate(lines, ctx, embed, max_lines=15, empty=False)
        else:
            embed.description = "Hmmm, seems like there's nothing here yet."
            await ctx.send(embed=embed)

    @group(aliases=("allowlist", "allow", "al", "wl"))
    async def whitelist(self, ctx: Context) -> None:
        """Group for whitelisting commands."""
        if not ctx.invoked_subcommand:
            await ctx.send_help(ctx.command)

    @group(aliases=("denylist", "deny", "bl", "dl"))
    async def blacklist(self, ctx: Context) -> None:
        """Group for blacklisting commands."""
        if not ctx.invoked_subcommand:
            await ctx.send_help(ctx.command)

    @whitelist.command(name="add", aliases=("a", "set"))
    async def allow_add(
            self,
            ctx: Context,
            list_type: ValidFilterListType,
            content: str,
            *,
            comment: Optional[str] = None,
    ) -> None:
        """Add an item to the specified allowlist."""
        await self._add_data(ctx, True, list_type, content, comment)

    @blacklist.command(name="add", aliases=("a", "set"))
    async def deny_add(
            self,
            ctx: Context,
            list_type: ValidFilterListType,
            content: str,
            *,
            comment: Optional[str] = None,
    ) -> None:
        """Add an item to the specified denylist."""
        await self._add_data(ctx, False, list_type, content, comment)

    @whitelist.command(name="remove", aliases=("delete", "rm",))
    async def allow_delete(self, ctx: Context, list_type: ValidFilterListType, content: str) -> None:
        """Remove an item from the specified allowlist."""
        await self._delete_data(ctx, True, list_type, content)

    @blacklist.command(name="remove", aliases=("delete", "rm",))
    async def deny_delete(self, ctx: Context, list_type: ValidFilterListType, content: str) -> None:
        """Remove an item from the specified denylist."""
        await self._delete_data(ctx, False, list_type, content)

    @whitelist.command(name="get", aliases=("list", "ls", "fetch", "show"))
    async def allow_get(self, ctx: Context, list_type: ValidFilterListType) -> None:
        """Get the contents of a specified allowlist."""
        await self._list_all_data(ctx, True, list_type)

    @blacklist.command(name="get", aliases=("list", "ls", "fetch", "show"))
    async def deny_get(self, ctx: Context, list_type: ValidFilterListType) -> None:
        """Get the contents of a specified denylist."""
        await self._list_all_data(ctx, False, list_type)

    def cog_check(self, ctx: Context) -> bool:
        """Only allow moderators to invoke the commands in this cog."""
        return with_role_check(ctx, *constants.MODERATION_ROLES)


def setup(bot: Bot) -> None:
    """Load the FilterLists cog."""
    bot.add_cog(FilterLists(bot))