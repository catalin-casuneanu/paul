from typing import List

from django.contrib.auth.models import Group
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from guardian.core import ObjectPermissionChecker
from rest_framework import serializers
from rest_framework_guardian.serializers import ObjectPermissionsAssignmentMixin

from api.models import Database, Table, TableColumn
from api.utils import snake_case
from api.serializers.users import OwnerSerializer, UserSerializer


class TableColumnSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(required=False)
    # choices = serializers.SerializerMethodField()

    class Meta:
        model = TableColumn
        fields = [
            "id",
            "name",
            "display_name",
            "field_type",
            "help_text",
            "required",
            "unique",
            "choices",
        ]

    # def get_choices(self, obj):
    #     if type(obj.choices) == list:
    #         return sorted([x for x in obj.choices if x])
    #     return []


class TableDatabaseSerializer(serializers.HyperlinkedModelSerializer):
    class Meta:
        model = Database
        fields = ["url", "id", "name", "slug"]


class TableCreateSerializer(ObjectPermissionsAssignmentMixin, serializers.ModelSerializer):
    database = serializers.PrimaryKeyRelatedField(queryset=Database.objects.all())
    owner = serializers.HiddenField(default=serializers.CurrentUserDefault())
    last_edit_user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    last_edit_date = serializers.HiddenField(default=timezone.now)
    active = serializers.BooleanField(default=True)
    fields = TableColumnSerializer(many=True, required=False)
    id = serializers.IntegerField(required=False)
    table_type = serializers.CharField(required=False)

    class Meta:
        model = Table
        fields = [
            "id",
            "database",
            "name",
            "owner",
            "fields",
            "default_fields",
            "filters",
            "last_edit_user",
            "last_edit_date",
            "active",
            "table_type",
        ]
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=model.objects.all(),
                fields=('name', 'database'),
                message=_("This table name is already being used.")
            )
        ]

    def validate(self, data):
        if "id" in data.keys():
            table = Table.objects.get(pk=data["id"])
            if table.entries.exists():
                if "fields" in data.keys():
                    for field in data.get("fields"):
                        if "id" in field.keys():
                            field_obj = TableColumn.objects.get(pk=field["id"])
                            if field_obj.field_type != field["field_type"]:
                                raise serializers.ValidationError(
                                    {
                                        "fields-{}".format(
                                            field["id"]
                                        ): _("Changing field type is not permited on a table with entries")
                                    }
                                )
        return data

    def create(self, validated_data):
        temp_fields = []
        if "fields" in validated_data.keys():
            temp_fields = validated_data.pop("fields")

        new_table = Table.objects.create(**validated_data)
        for i in temp_fields:
            if "display_name" not in i.keys():
                i["display_name"] = i["name"]
                i["name"] = snake_case(i["name"])
            if "name" not in i.keys():
                i["name"] = snake_case(i["display_name"])

            TableColumn.objects.create(table=new_table, **i)

        return new_table

    def update(self, instance, validated_data):
        if self.partial:
            if validated_data.get('filters'):
                filters = validated_data.pop('filters')
                if filters:
                    Table.objects.filter(pk=instance.pk).update(**{'filters': filters})
            
            if validated_data.get('default_fields'):
                default_fields = validated_data.pop('default_fields')
                if default_fields:
                    for field in default_fields:
                        instance.default_fields.add(field)

            # Toggle the 'active' field
            if 'active' in validated_data:
                Table.objects.filter(pk=instance.pk).update(active=validated_data['active'])

            instance.refresh_from_db()
        else:
            instance.name = validated_data.get("name")
            instance.active = validated_data.get("active")
            instance.database = validated_data.get("database")
            instance.last_edit_user = self.context["request"].user
            if "fields" in validated_data.keys():
                # Check to see if we need to delete any field
                old_fields_ids = set(instance.fields.values_list("id", flat=True))
                new_fields_ids = set([x.get("id") for x in validated_data.get("fields")])
                for id_to_remove in old_fields_ids - new_fields_ids:
                    field = TableColumn.objects.get(pk=id_to_remove)
                    field_name = field.name
                    field.delete()
                    for entry in instance.entries.all():
                        del entry.data[field_name]
                        entry.save()
                # Create or update fields
                for field in validated_data.pop("fields"):
                    if "id" in field.keys():
                        field_obj = TableColumn.objects.get(pk=field["id"])
                        old_name = field_obj.name
                        new_name = field["name"]
                        if old_name != new_name:

                            for entry in instance.entries.all():
                                entry.data[new_name] = entry.data[old_name]
                                del entry.data[old_name]
                                entry.save()
                        field_obj.__dict__.update(field)
                        field_obj.save()
                    else:

                        field["table"] = instance
                        field["name"] = snake_case(field["display_name"])
                        TableColumn.objects.create(**field)

            instance.save()
        return instance

    def get_permissions_map(self, created):
        current_user = self.context["request"].user
        admins = Group.objects.get(name="admin")

        return {
            "view_table": [current_user, admins],
            "change_table": [current_user, admins],
            "delete_table": [current_user, admins],
            "update_content": [current_user, admins],
        }


class TableSerializer(serializers.ModelSerializer):
    database = TableDatabaseSerializer()
    owner = OwnerSerializer(read_only=True)
    last_edit_user = UserSerializer(read_only=True)
    fields = TableColumnSerializer(many=True)
    entries = serializers.SerializerMethodField()
    default_fields = serializers.SerializerMethodField()
    current_user_permissions = serializers.SerializerMethodField()

    class Meta:
        model = Table
        fields = [
            "url",
            "id",
            "name",
            "entries",
            "entries_count",
            "database",
            "slug",
            "owner",
            "last_edit_user",
            "last_edit_date",
            "date_created",
            "active",
            "default_fields",
            "fields",
            "filters",
            "current_user_permissions",
        ]

    def get_default_fields(self, obj):
        if obj.default_fields.all():
            return [x for x in obj.default_fields.values_list("name", flat=True).order_by("id")]
        return [x for x in obj.fields.values_list("name", flat=True).order_by("id")]

    def get_entries(self, obj):
        return self.context["request"].build_absolute_uri(reverse("table-entries-list", kwargs={"table_pk": obj.pk}))

    def get_current_user_permissions(self, obj: Table) -> List[str]:
        request = self.context.get('request', None)
        if not request or not request.user:
            return [""]
        
        table_permissions = []
        checker = ObjectPermissionChecker(request.user)
        user_perms = checker.get_perms(obj)
        if "change_table" in user_perms:
            table_permissions.append("change_table")
        elif "update_content" in user_perms:
            table_permissions.append("update_content")
        elif "view_table" in user_perms:
            table_permissions.append("view_table")
        else:
            table_permissions.append("")
        return table_permissions


class TableSearchCountSerializer(serializers.Serializer):
    table = serializers.IntegerField()
    count = serializers.IntegerField()
