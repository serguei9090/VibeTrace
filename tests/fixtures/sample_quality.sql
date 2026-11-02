-- Test fixture for SQL quality, style, and structure
select
    *,
    id,
    name
from users
where active = 1;

select
    u.id,
    (
        select count(*) from orders as o
        where o.user_id = u.id and o.status in (
            select s.name from statuses as s
            where s.active = 1
        )
    )
from users as u;
