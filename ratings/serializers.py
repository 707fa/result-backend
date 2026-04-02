from rest_framework import serializers
from users.models import User


class RatingItemSerializer(serializers.ModelSerializer):
    place = serializers.IntegerField(read_only=True)
    group_id = serializers.IntegerField(source="group.id", read_only=True)
    group_title = serializers.CharField(source="group.title", read_only=True)

    class Meta:
        model = User
        fields = (
            "place",
            "id",
            "full_name",
            "points",
            "group_id",
            "group_title",
            "avatar",
        )


class MyRatingSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    full_name = serializers.CharField()
    points = serializers.IntegerField()
    group = serializers.DictField(allow_null=True)
    places = serializers.DictField()